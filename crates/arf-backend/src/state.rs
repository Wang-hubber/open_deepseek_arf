//! `StateBackend` — in-memory, thread-isolated backend (Phase 11 / 11.2).
//!
//! Pure-RAM storage with no host FS or shell access. Safe default for
//! untrusted prompts. Used as the default backend in Phase 11-M4.
//!
//! **Thread safety**: backed by `DashMap` for concurrent reads/writes
//! without blocking the runtime. Distinct from `HashMap + RwLock` which
//! holds a lock across writes.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use chrono::Utc;
use dashmap::DashMap;

use crate::error::BackendError;
use crate::path::BackendPath;
use crate::protocol::{BackendProtocol, SandboxBackendProtocol};
use crate::types::{
    DeleteResult, EditResult, ExecuteResponse, FileData, FileInfo, GlobResult, GrepMatch,
    GrepResult, LsResult, ReadResult, WriteResult,
};

/// Default `StateBackend` instance id (used by `id()`).
pub const DEFAULT_STATE_BACKEND_ID: &str = "state-default";

/// In-memory, thread-isolated backend.
///
/// Stores files in a `DashMap<BackendPath, FileData>`. No host FS, no
/// shell. Files persist only for the lifetime of the `StateBackend`
/// instance — typically one per session.
pub struct StateBackend {
    files: Arc<DashMap<BackendPath, FileData>>,
    id: String,
}

impl StateBackend {
    /// Create an empty `StateBackend` with the default id.
    pub fn new() -> Self {
        Self {
            files: Arc::new(DashMap::new()),
            id: DEFAULT_STATE_BACKEND_ID.into(),
        }
    }

    /// Create a `StateBackend` pre-populated with `seed` files.
    ///
    /// Useful for tests and for replaying a previous session's state.
    /// Seed paths MUST be valid `BackendPath`s.
    pub fn with_seed(seed: HashMap<BackendPath, String>) -> Self {
        let now = Utc::now();
        let backend = Self::new();
        for (path, content) in seed {
            let data = FileData {
                content,
                encoding: "utf-8".into(),
                created_at: Some(now),
                modified_at: Some(now),
            };
            backend.files.insert(path, data);
        }
        backend
    }

    /// Create a `StateBackend` with a custom id (for sandbox identity).
    pub fn with_id(id: impl Into<String>) -> Self {
        Self {
            files: Arc::new(DashMap::new()),
            id: id.into(),
        }
    }

    /// Return the number of files currently stored.
    pub fn len(&self) -> usize {
        self.files.len()
    }

    /// Return true if no files are stored.
    pub fn is_empty(&self) -> bool {
        self.files.is_empty()
    }
}

impl Default for StateBackend {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl BackendProtocol for StateBackend {
    async fn ls(&self, path: &BackendPath) -> Result<LsResult, BackendError> {
        let prefix = path.as_str();
        let entries: Vec<FileInfo> = self
            .files
            .iter()
            .filter_map(|kv| {
                let key = kv.key().as_str();
                if key == prefix {
                    return None;
                }
                if !key.starts_with(prefix) {
                    return None;
                }
                // Ensure directory-boundary match: either prefix ends with `/`
                // or next char after prefix is `/`.
                if !prefix.ends_with('/') && key.as_bytes().get(prefix.len()) != Some(&b'/') {
                    return None;
                }
                let data = kv.value();
                Some(FileInfo {
                    path: key.to_string(),
                    is_dir: false,
                    size: data.content.len() as u64,
                    modified_at: data.modified_at,
                })
            })
            .collect();
        Ok(LsResult {
            error: None,
            entries: Some(entries),
        })
    }

    async fn read(
        &self,
        path: &BackendPath,
        offset: u32,
        limit: u32,
    ) -> Result<ReadResult, BackendError> {
        let Some(data) = self.files.get(path).map(|r| r.value().clone()) else {
            return Ok(ReadResult {
                error: Some("file_not_found".into()),
                file_data: None,
            });
        };
        // Apply offset/limit on lines.
        let lines: Vec<&str> = data.content.lines().collect();
        let start = (offset as usize).min(lines.len());
        let end = if limit == 0 {
            lines.len()
        } else {
            (start + limit as usize).min(lines.len())
        };
        let slice: String = lines[start..end].join("\n");
        Ok(ReadResult {
            error: None,
            file_data: Some(FileData {
                content: slice,
                encoding: data.encoding,
                created_at: data.created_at,
                modified_at: data.modified_at,
            }),
        })
    }

    async fn write(&self, path: &BackendPath, content: &str) -> Result<WriteResult, BackendError> {
        let now = Utc::now();
        let created_at = self
            .files
            .get(path)
            .map(|r| r.value().created_at.unwrap_or(now));
        let data = FileData {
            content: content.to_string(),
            encoding: "utf-8".into(),
            created_at,
            modified_at: Some(now),
        };
        self.files.insert(path.clone(), data);
        Ok(WriteResult {
            error: None,
            path: Some(path.to_string()),
        })
    }

    async fn edit(
        &self,
        path: &BackendPath,
        old_string: &str,
        new_string: &str,
        replace_all: bool,
    ) -> Result<EditResult, BackendError> {
        let Some(mut kv) = self.files.get_mut(path) else {
            return Err(BackendError::FileNotFound {
                path: path.to_string(),
                code: "file_not_found",
            });
        };
        let data = kv.value_mut();
        let occurrences = data.content.matches(old_string).count();
        if occurrences == 0 {
            return Err(BackendError::EditMatch {
                reason: format!("old_string not found in {path}"),
            });
        }
        if !replace_all && occurrences > 1 {
            return Err(BackendError::EditMatch {
                reason: format!(
                    "old_string matched {occurrences} times in {path} (not unique; pass replace_all=true)"
                ),
            });
        }
        let new_content = if replace_all {
            data.content.replace(old_string, new_string)
        } else {
            data.content.replacen(old_string, new_string, 1)
        };
        let actual_replacements = if replace_all {
            occurrences as u32
        } else {
            1
        };
        data.content = new_content;
        data.modified_at = Some(Utc::now());
        Ok(EditResult {
            error: None,
            path: Some(path.to_string()),
            occurrences: actual_replacements,
        })
    }

    async fn delete(&self, path: &BackendPath) -> Result<DeleteResult, BackendError> {
        // Recursive delete: remove exact match + any key sharing `path + "/"` prefix.
        let prefix = path.as_str();
        let mut to_remove: Vec<BackendPath> = Vec::new();
        for kv in self.files.iter() {
            let k = kv.key().as_str();
            if k == prefix || k.starts_with(&format!("{prefix}/")) {
                to_remove.push(kv.key().clone());
            }
        }
        if to_remove.is_empty() {
            return Err(BackendError::FileNotFound {
                path: path.to_string(),
                code: "file_not_found",
            });
        }
        for key in to_remove {
            self.files.remove(&key);
        }
        Ok(DeleteResult {
            error: None,
            path: Some(path.to_string()),
        })
    }

    async fn glob(
        &self,
        pattern: &str,
        _base: Option<&BackendPath>,
    ) -> Result<GlobResult, BackendError> {
        // Minimal glob: match using a simple `*` and `**` interpreter.
        // Production will use `globset` in FilesystemBackend (11.3).
        let matches: Vec<FileInfo> = self
            .files
            .iter()
            .filter(|kv| glob_match_simple(pattern, kv.key().as_str()))
            .map(|kv| {
                let data = kv.value();
                FileInfo {
                    path: kv.key().to_string(),
                    is_dir: false,
                    size: data.content.len() as u64,
                    modified_at: data.modified_at,
                }
            })
            .collect();
        Ok(GlobResult {
            error: None,
            matches: Some(matches),
            truncated: false,
        })
    }

    async fn grep(
        &self,
        pattern: &str,
        path: Option<&BackendPath>,
        glob_filter: Option<&str>,
    ) -> Result<GrepResult, BackendError> {
        let path_prefix = path.map(|p| p.as_str().to_string()).unwrap_or_default();
        let mut matches: Vec<GrepMatch> = Vec::new();
        for kv in self.files.iter() {
            let p = kv.key().as_str();
            if !path_prefix.is_empty() && !p.starts_with(&path_prefix) {
                continue;
            }
            if let Some(filter) = glob_filter {
                if !glob_match_simple(filter, p) {
                    continue;
                }
            }
            for (idx, line) in kv.value().content.lines().enumerate() {
                if line.contains(pattern) {
                    matches.push(GrepMatch {
                        path: p.to_string(),
                        line: (idx + 1) as u32,
                        text: line.to_string(),
                    });
                }
            }
        }
        Ok(GrepResult {
            error: None,
            matches: Some(matches),
            truncated: false,
        })
    }

    async fn upload_files(
        &self,
        files: Vec<(BackendPath, Vec<u8>)>,
    ) -> Result<Vec<Result<(), BackendError>>, BackendError> {
        let mut results = Vec::with_capacity(files.len());
        for (path, bytes) in files {
            let content = match String::from_utf8(bytes.clone()) {
                Ok(s) => s,
                Err(_) => {
                    results.push(Err(BackendError::Io(format!(
                        "non-UTF8 bytes at {path} (base64 encoding not yet supported)"
                    ))));
                    continue;
                }
            };
            self.write(&path, &content).await?;
            results.push(Ok(()));
        }
        Ok(results)
    }
}

#[async_trait]
impl SandboxBackendProtocol for StateBackend {
    fn id(&self) -> &str {
        &self.id
    }

    async fn execute(
        &self,
        command: &str,
        _timeout_ms: Option<u64>,
    ) -> Result<ExecuteResponse, BackendError> {
        // StateBackend is the SAFE default — reject all shell execution.
        // FilesystemBackend / LocalShellBackend (11.3 / M4) override this.
        tracing::debug!(command, "StateBackend::execute rejected (safe default)");
        Err(BackendError::Unsupported("execute"))
    }
}

// ---------------------------------------------------------------------------
// Internal: minimal glob matcher (sufficient for StateBackend tests).
// Production will use `globset` crate in FilesystemBackend (11.3).
// ---------------------------------------------------------------------------

fn glob_match_simple(pattern: &str, path: &str) -> bool {
    fn char_match(pat: &[u8], path: &[u8]) -> bool {
        let rest_pat = |i: usize| pat.get(i..).unwrap_or(&[]);
        let rest_path = |i: usize| path.get(i..).unwrap_or(&[]);
        match (pat.first(), path.first()) {
            (None, None) => true,
            (None, Some(_)) => false,
            (Some(b'*'), _) => {
                if pat.get(1) == Some(&b'*') {
                    if path.is_empty() {
                        char_match(rest_pat(2), path)
                    } else {
                        char_match(rest_pat(2), path) || char_match(pat, rest_path(1))
                    }
                } else if path.is_empty() || path[0] == b'/' {
                    char_match(rest_pat(1), path)
                } else {
                    char_match(rest_pat(1), path) || char_match(pat, rest_path(1))
                }
            }
            (Some(b'?'), Some(_)) => char_match(rest_pat(1), rest_path(1)),
            (Some(a), Some(b)) if a == b => char_match(rest_pat(1), rest_path(1)),
            (Some(_), None) => false,
            (Some(_), Some(_)) => false,
        }
    }
    char_match(pattern.as_bytes(), path.as_bytes())
}