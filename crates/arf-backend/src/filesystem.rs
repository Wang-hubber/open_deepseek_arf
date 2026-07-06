//! `FilesystemBackend` — real-FS backend with root-directory sandboxing.
//!
//! Maps `BackendPath` (`/foo/bar`) onto `<root_dir>/foo/bar` on the host
//! filesystem. All operations are constrained to `root_dir`; any attempt
//! to escape (via `..`, absolute paths outside root, or symlinks pointing
//! outside root) returns `BackendError::PermissionDenied`.

use std::path::{Component, Path, PathBuf};
use std::sync::Arc;

use async_trait::async_trait;
use chrono::{DateTime, Utc};
use globset::{Glob, GlobMatcher};
use regex::Regex;
use tokio::fs;
use tokio::sync::Mutex;

use crate::error::BackendError;
use crate::path::BackendPath;
use crate::protocol::BackendProtocol;
use crate::types::{
    DeleteResult, EditResult, FileData, FileInfo, GlobResult, GrepMatch, GrepResult, LsResult,
    ReadResult, WriteResult,
};

/// Real-FS backend rooted at a single directory.
#[derive(Clone)]
pub struct FilesystemBackend {
    /// Absolute path to the sandbox root.
    root: PathBuf,
    /// Cached glob matchers (avoid recompiling on each call).
    glob_cache: Arc<Mutex<Vec<(String, GlobMatcher)>>>,
}

impl FilesystemBackend {
    /// Create a `FilesystemBackend` rooted at `root_dir`.
    ///
    /// `root_dir` MUST be an absolute, existing directory. The backend
    /// refuses operations that escape this root.
    pub fn new(root_dir: impl Into<PathBuf>) -> Result<Self, BackendError> {
        let root = root_dir.into();
        if !root.is_absolute() {
            return Err(BackendError::InvalidPath {
                path: root.to_string_lossy().into(),
                code: "invalid_path",
            });
        }
        let meta = std::fs::metadata(&root).map_err(|e| BackendError::Io(e.to_string()))?;
        if !meta.is_dir() {
            return Err(BackendError::Io(format!(
                "root is not a directory: {}",
                root.display()
            )));
        }
        Ok(Self {
            root,
            glob_cache: Arc::new(Mutex::new(Vec::new())),
        })
    }

    /// Return the absolute root path (for debugging/tests).
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Map a `BackendPath` to its on-disk absolute path, verifying that
    /// the resolved path stays inside the root.
    async fn resolve(&self, path: &BackendPath) -> Result<PathBuf, BackendError> {
        let stripped = path.as_str().trim_start_matches('/');
        let candidate = self.root.join(stripped);
        let canonical = match fs::canonicalize(&candidate).await {
            Ok(p) => p,
            Err(_) => normalize_lexically(&candidate),
        };
        let root_canonical = fs::canonicalize(&self.root)
            .await
            .map_err(|e| BackendError::Io(e.to_string()))?;
        if !canonical.starts_with(&root_canonical) {
            return Err(BackendError::PermissionDenied {
                path: path.to_string(),
                reason: format!("path escapes root: {}", canonical.display()),
                code: "permission_denied",
            });
        }
        Ok(canonical)
    }

    /// Compile a glob pattern, caching the result.
    async fn compile_glob(&self, pattern: &str) -> Result<GlobMatcher, BackendError> {
        {
            let cache = self.glob_cache.lock().await;
            if let Some((_, matcher)) = cache.iter().find(|(p, _)| p == pattern) {
                return Ok(matcher.clone());
            }
        }
        let glob =
            Glob::new(pattern).map_err(|e| BackendError::Io(format!("bad glob: {e}")))?;
        let matcher = glob.compile_matcher();
        let mut cache = self.glob_cache.lock().await;
        cache.push((pattern.to_string(), matcher.clone()));
        Ok(matcher)
    }
}

#[async_trait]
impl BackendProtocol for FilesystemBackend {
    async fn ls(&self, path: &BackendPath) -> Result<LsResult, BackendError> {
        let abs = self.resolve(path).await?;
        let meta = fs::metadata(&abs)
            .await
            .map_err(|e| BackendError::Io(e.to_string()))?;
        if !meta.is_dir() {
            return Err(BackendError::NotADirectory {
                path: path.to_string(),
            });
        }
        let mut entries = fs::read_dir(&abs)
            .await
            .map_err(|e| BackendError::Io(e.to_string()))?;
        let mut results = Vec::new();
        while let Some(entry) = entries
            .next_entry()
            .await
            .map_err(|e| BackendError::Io(e.to_string()))?
        {
            let file_name = entry.file_name();
            let entry_path = path.join_str(&file_name.to_string_lossy());
            let meta = entry
                .metadata()
                .await
                .map_err(|e| BackendError::Io(e.to_string()))?;
            let modified_at = meta_to_datetime(meta.modified().ok());
            results.push(FileInfo {
                path: entry_path.as_str().to_string(),
                is_dir: meta.is_dir(),
                size: meta.len(),
                modified_at,
            });
        }
        Ok(LsResult {
            error: None,
            entries: Some(results),
        })
    }

    async fn read(
        &self,
        path: &BackendPath,
        offset: u32,
        limit: u32,
    ) -> Result<ReadResult, BackendError> {
        let abs = self.resolve(path).await?;
        let meta = match fs::metadata(&abs).await {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                return Ok(ReadResult {
                    error: Some("file_not_found".into()),
                    file_data: None,
                });
            }
            Err(e) => return Err(BackendError::Io(e.to_string())),
        };
        if meta.is_dir() {
            return Err(BackendError::IsDirectory {
                path: path.to_string(),
                code: "is_directory",
            });
        }
        if meta.file_type().is_symlink() {
            return Err(BackendError::PermissionDenied {
                path: path.to_string(),
                reason: "symlinks not followed for read".into(),
                code: "permission_denied",
            });
        }
        let bytes = fs::read(&abs)
            .await
            .map_err(|e| BackendError::Io(e.to_string()))?;
        let content = match String::from_utf8(bytes) {
            Ok(s) => s,
            Err(_) => {
                return Ok(ReadResult {
                    error: Some("file is binary; base64 encoding not yet supported".into()),
                    file_data: None,
                });
            }
        };
        let lines: Vec<&str> = content.lines().collect();
        let start = (offset as usize).min(lines.len());
        let end = if limit == 0 {
            lines.len()
        } else {
            (start + limit as usize).min(lines.len())
        };
        let slice = lines[start..end].join("\n");
        Ok(ReadResult {
            error: None,
            file_data: Some(FileData {
                content: slice,
                encoding: "utf-8".into(),
                created_at: meta_to_datetime(meta.created().ok()),
                modified_at: meta_to_datetime(meta.modified().ok()),
            }),
        })
    }

    async fn write(&self, path: &BackendPath, content: &str) -> Result<WriteResult, BackendError> {
        let abs = self.resolve(path).await?;
        if let Some(parent) = abs.parent() {
            fs::create_dir_all(parent)
                .await
                .map_err(|e| BackendError::Io(e.to_string()))?;
        }
        fs::write(&abs, content)
            .await
            .map_err(|e| BackendError::Io(e.to_string()))?;
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
        let abs = self.resolve(path).await?;
        let content = fs::read_to_string(&abs)
            .await
            .map_err(|e| BackendError::Io(e.to_string()))?;
        let occurrences = content.matches(old_string).count();
        if occurrences == 0 {
            return Err(BackendError::EditMatch {
                reason: format!("old_string not found in {path}"),
            });
        }
        if !replace_all && occurrences > 1 {
            return Err(BackendError::EditMatch {
                reason: format!(
                    "old_string matched {occurrences} times in {path} (not unique)"
                ),
            });
        }
        let new_content = if replace_all {
            content.replace(old_string, new_string)
        } else {
            content.replacen(old_string, new_string, 1)
        };
        let actual_replacements = if replace_all {
            occurrences as u32
        } else {
            1
        };
        fs::write(&abs, new_content)
            .await
            .map_err(|e| BackendError::Io(e.to_string()))?;
        Ok(EditResult {
            error: None,
            path: Some(path.to_string()),
            occurrences: actual_replacements,
        })
    }

    async fn delete(&self, path: &BackendPath) -> Result<DeleteResult, BackendError> {
        let abs = self.resolve(path).await?;
        let meta = fs::metadata(&abs)
            .await
            .map_err(|e| BackendError::Io(e.to_string()))?;
        if meta.is_dir() {
            fs::remove_dir_all(&abs)
                .await
                .map_err(|e| BackendError::Io(e.to_string()))?;
        } else {
            fs::remove_file(&abs)
                .await
                .map_err(|e| BackendError::Io(e.to_string()))?;
        }
        Ok(DeleteResult {
            error: None,
            path: Some(path.to_string()),
        })
    }

    async fn glob(
        &self,
        pattern: &str,
        base: Option<&BackendPath>,
    ) -> Result<GlobResult, BackendError> {
        let base_abs = match base {
            Some(p) => Some(self.resolve(p).await?),
            None => None,
        };
        let base_abs = base_abs.unwrap_or_else(|| self.root.clone());
        let matcher = self.compile_glob(pattern).await?;
        let mut results = Vec::new();
        for entry in walkdir::WalkDir::new(&base_abs)
            .follow_links(false)
            .into_iter()
            .filter_entry(|e| !is_hidden(e))
        {
            let entry = entry.map_err(|e| BackendError::Io(e.to_string()))?;
            let path = entry.path();
            if !matcher.is_match(path) {
                continue;
            }
            let rel = path.strip_prefix(&self.root).unwrap_or(path);
            let backend_path_str = format!("/{}", rel.to_string_lossy());
            // If the relative path can't be normalized (contains ..), skip it.
            if BackendPath::new(&backend_path_str).is_err() {
                continue;
            }
            let meta = entry
                .metadata()
                .map_err(|e| BackendError::Io(e.to_string()))?;
            let modified_at = meta_to_datetime(meta.modified().ok());
            results.push(FileInfo {
                path: backend_path_str,
                is_dir: meta.is_dir(),
                size: meta.len(),
                modified_at,
            });
        }
        Ok(GlobResult {
            error: None,
            matches: Some(results),
            truncated: false,
        })
    }

    async fn grep(
        &self,
        pattern: &str,
        path: Option<&BackendPath>,
        glob_filter: Option<&str>,
    ) -> Result<GrepResult, BackendError> {
        let base = match path {
            Some(p) => self.resolve(p).await?,
            None => self.root.clone(),
        };
        let glob_matcher = match glob_filter {
            Some(g) => Some(self.compile_glob(g).await?),
            None => None,
        };
        let matcher = Regex::new(&regex::escape(pattern))
            .map_err(|e| BackendError::Io(format!("bad grep pattern: {e}")))?;
        let mut results = Vec::new();
        for entry in walkdir::WalkDir::new(&base).follow_links(false) {
            let entry = match entry {
                Ok(e) => e,
                Err(_) => continue,
            };
            let entry_path = entry.path();
            if !entry_path.is_file() {
                continue;
            }
            let rel = entry_path.strip_prefix(&self.root).unwrap_or(entry_path);
            if let Some(ref m) = glob_matcher {
                if !m.is_match(rel) {
                    continue;
                }
            }
            let matches_in_file = grep_file(&matcher, entry_path).await?;
            for (line_num, line) in matches_in_file {
                let backend_path_str = format!("/{}", rel.to_string_lossy());
                if BackendPath::new(&backend_path_str).is_err() {
                    continue;
                }
                results.push(GrepMatch {
                    path: backend_path_str,
                    line: line_num as u32,
                    text: line,
                });
            }
        }
        Ok(GrepResult {
            error: None,
            matches: Some(results),
            truncated: false,
        })
    }

    async fn upload_files(
        &self,
        files: Vec<(BackendPath, Vec<u8>)>,
    ) -> Result<Vec<Result<(), BackendError>>, BackendError> {
        let mut results = Vec::with_capacity(files.len());
        for (path, bytes) in files {
            let abs = match self.resolve(&path).await {
                Ok(p) => p,
                Err(e) => {
                    results.push(Err(e));
                    continue;
                }
            };
            if let Some(parent) = abs.parent() {
                if let Err(e) = fs::create_dir_all(parent).await {
                    results.push(Err(BackendError::Io(e.to_string())));
                    continue;
                }
            }
            if let Err(e) = fs::write(&abs, &bytes).await {
                results.push(Err(BackendError::Io(e.to_string())));
                continue;
            }
            results.push(Ok(()));
        }
        Ok(results)
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn normalize_lexically(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Prefix(_) | Component::RootDir => result.push(component),
            Component::CurDir => {}
            Component::ParentDir => {
                if !result.pop() {
                    result.push("..");
                }
            }
            Component::Normal(c) => result.push(c),
        }
    }
    result
}

fn is_hidden(entry: &walkdir::DirEntry) -> bool {
    // Skip hidden files at depth > 0. Depth-0 (the walk root itself) is
    // never hidden — this lets `tempdir` names like `.tmpXXXXXX` work
    // as sandbox roots.
    if entry.depth() == 0 {
        return false;
    }
    entry
        .file_name()
        .to_str()
        .map(|s| s.starts_with('.'))
        .unwrap_or(false)
}

fn meta_to_datetime(t: Option<std::time::SystemTime>) -> Option<DateTime<Utc>> {
    let t = t?;
    let d = t.duration_since(std::time::UNIX_EPOCH).ok()?;
    DateTime::<Utc>::from_timestamp(d.as_secs() as i64, d.subsec_nanos())
}

async fn grep_file(
    matcher: &Regex,
    path: &Path,
) -> Result<Vec<(usize, String)>, BackendError> {
    let matcher = matcher.clone();
    let path = path.to_path_buf();
    let result = tokio::task::spawn_blocking(move || -> Result<Vec<(usize, String)>, String> {
        let content = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
        let mut hits = Vec::new();
        for (line_num, line) in content.lines().enumerate() {
            if matcher.is_match(line) {
                hits.push((line_num + 1, line.to_string()));
            }
        }
        Ok(hits)
    })
    .await
    .map_err(|e| BackendError::Io(format!("spawn_blocking join: {e}")))?
    .map_err(BackendError::Io)?;
    Ok(result)
}

// ---------------------------------------------------------------------------
// BackendPath extension
// ---------------------------------------------------------------------------

trait BackendPathExt {
    fn join_str(&self, segment: &str) -> BackendPath;
}

impl BackendPathExt for BackendPath {
    fn join_str(&self, segment: &str) -> BackendPath {
        let sep = if self.as_str().ends_with('/') { "" } else { "/" };
        let raw = format!("{}{}{}", self.as_str(), sep, segment);
        BackendPath::new(&raw).unwrap_or_else(|_| self.clone())
    }
}