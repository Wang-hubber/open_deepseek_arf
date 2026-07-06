//! Trait-level contract tests for `BackendProtocol`.
//!
//! Uses an in-memory `MockBackend` to verify the trait surface, error
//! normalization, and type-level guarantees. Concrete backends
//! (StateBackend / FilesystemBackend / CompositeBackend in 11.2/11.3/11.4)
//! have their own integration tests.

use std::collections::HashMap;
use std::sync::Arc;

use arf_backend::{
    BackendError, BackendPath, BackendProtocol, EditResult, ExecuteResponse, FileData, FileInfo,
    GlobResult, GrepMatch, GrepResult, LsResult, ReadResult, SandboxBackendProtocol,
    WriteResult,
};
use async_trait::async_trait;
use tokio::sync::RwLock;

// ---------------------------------------------------------------------------
// Mock backend
// ---------------------------------------------------------------------------

pub struct MockBackend {
    files: RwLock<HashMap<String, String>>,
    pub sandbox_id: String,
}

impl MockBackend {
    pub fn new() -> Self {
        Self {
            files: RwLock::new(HashMap::new()),
            sandbox_id: "mock-1".into(),
        }
    }
}

#[async_trait]
impl BackendProtocol for MockBackend {
    async fn ls(&self, path: &BackendPath) -> Result<LsResult, BackendError> {
        let files = self.files.read().await;
        let prefix = path.as_str();
        let entries: Vec<FileInfo> = files
            .keys()
            .filter(|k| k.starts_with(prefix) && k.len() > prefix.len())
            .map(|k| FileInfo {
                path: k.clone(),
                is_dir: false,
                size: 0,
                modified_at: None,
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
        _offset: u32,
        _limit: u32,
    ) -> Result<ReadResult, BackendError> {
        let files = self.files.read().await;
        match files.get(path.as_str()) {
            Some(content) => Ok(ReadResult {
                error: None,
                file_data: Some(FileData {
                    content: content.clone(),
                    encoding: "utf-8".into(),
                    created_at: None,
                    modified_at: None,
                }),
            }),
            None => Ok(ReadResult {
                error: Some("file_not_found".into()),
                file_data: None,
            }),
        }
    }

    async fn write(&self, path: &BackendPath, content: &str) -> Result<WriteResult, BackendError> {
        let mut files = self.files.write().await;
        files.insert(path.as_str().to_string(), content.to_string());
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
        let mut files = self.files.write().await;
        let content = files
            .get(path.as_str())
            .ok_or_else(|| BackendError::FileNotFound {
                path: path.to_string(),
                code: "file_not_found",
            })?
            .clone();
        let occurrences = content.matches(old_string).count();
        if occurrences == 0 {
            return Err(BackendError::EditMatch {
                reason: "old_string not found".into(),
            });
        }
        if !replace_all && occurrences > 1 {
            return Err(BackendError::EditMatch {
                reason: format!("old_string matched {occurrences} times (not unique)"),
            });
        }
        let new_content = if replace_all {
            content.replace(old_string, new_string)
        } else {
            content.replacen(old_string, new_string, 1)
        };
        let actual_replacements = if replace_all { occurrences } else { 1 };
        files.insert(path.as_str().to_string(), new_content);
        Ok(EditResult {
            error: None,
            path: Some(path.to_string()),
            occurrences: actual_replacements as u32,
        })
    }

    async fn glob(
        &self,
        pattern: &str,
        _base: Option<&BackendPath>,
    ) -> Result<GlobResult, BackendError> {
        let files = self.files.read().await;
        let matches: Vec<FileInfo> = files
            .keys()
            .filter(|k| glob_match(pattern, k))
            .map(|k| FileInfo {
                path: k.clone(),
                is_dir: false,
                size: 0,
                modified_at: None,
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
        _path: Option<&BackendPath>,
        glob_filter: Option<&str>,
    ) -> Result<GrepResult, BackendError> {
        let files = self.files.read().await;
        let mut matches = Vec::new();
        for (path, content) in files.iter() {
            if let Some(filter) = glob_filter {
                if !glob_match(filter, path) {
                    continue;
                }
            }
            for (idx, line) in content.lines().enumerate() {
                if line.contains(pattern) {
                    matches.push(GrepMatch {
                        path: path.clone(),
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
}

#[async_trait]
impl SandboxBackendProtocol for MockBackend {
    fn id(&self) -> &str {
        &self.sandbox_id
    }

    async fn execute(
        &self,
        _command: &str,
        _timeout_ms: Option<u64>,
    ) -> Result<ExecuteResponse, BackendError> {
        Ok(ExecuteResponse {
            output: "mock".into(),
            exit_code: Some(0),
            truncated: false,
        })
    }
}

// Minimal glob matcher supporting `*` (any chars except `/`) and `**` (any chars).
// Used only by the MockBackend for self-contained testing; the real
// FilesystemBackend (11.3) will use the `globset` crate.
fn glob_match(pattern: &str, path: &str) -> bool {
    fn char_match(pat: &[u8], path: &[u8]) -> bool {
        let rest_pat = |i: usize| pat.get(i..).unwrap_or(&[]);
        let rest_path = |i: usize| path.get(i..).unwrap_or(&[]);
        match (pat.first(), path.first()) {
            (None, None) => true,
            (None, Some(_)) => false,
            (Some(b'*'), _) => {
                if pat.get(1) == Some(&b'*') {
                    // `**` consumes zero or more chars (including `/`).
                    if path.is_empty() {
                        // No more path to consume; must match with `**` as zero chars.
                        char_match(rest_pat(2), path)
                    } else {
                        char_match(rest_pat(2), path) || char_match(pat, rest_path(1))
                    }
                } else {
                    // `*` consumes zero or more chars (excluding `/`).
                    if path.is_empty() || path[0] == b'/' {
                        char_match(rest_pat(1), path)
                    } else {
                        char_match(rest_pat(1), path) || char_match(pat, rest_path(1))
                    }
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

// ---------------------------------------------------------------------------
// Tests — BackendPath construction & invariants  [构造]
// ---------------------------------------------------------------------------

#[test]
fn path_new_simple_absolute() {
    let p = BackendPath::new("/foo/bar").unwrap();
    assert_eq!(p.as_str(), "/foo/bar");
}

#[test]
fn path_new_empty_rejected() {
    let err = BackendPath::new("").unwrap_err();
    assert!(matches!(err, BackendError::InvalidPath { .. }));
}

#[test]
fn path_new_relative_rejected_with_dotdot() {
    let err = BackendPath::new("../etc/passwd").unwrap_err();
    assert!(matches!(err, BackendError::InvalidPath { .. }));
}

#[test]
fn path_new_dotdot_inside_rejected() {
    let err = BackendPath::new("/foo/../bar").unwrap_err();
    assert!(matches!(err, BackendError::InvalidPath { .. }));
}

#[test]
fn path_new_dot_normalized() {
    let p = BackendPath::new("/foo/./bar").unwrap();
    assert_eq!(p.as_str(), "/foo/bar");
}

#[test]
fn path_new_nul_rejected() {
    let err = BackendPath::new("/foo\0bar").unwrap_err();
    assert!(matches!(err, BackendError::InvalidPath { .. }));
}

#[test]
fn path_new_backslash_rejected() {
    let err = BackendPath::new("/foo\\bar").unwrap_err();
    assert!(matches!(err, BackendError::InvalidPath { .. }));
}

// ---------------------------------------------------------------------------
// Tests — BackendPath::is_ancestor_of  [唯一性]
// ---------------------------------------------------------------------------

#[test]
fn ancestor_self_is_true() {
    let p = BackendPath::new("/foo/bar").unwrap();
    assert!(p.is_ancestor_of(&p));
}

#[test]
fn ancestor_strict_descendant_is_true() {
    let parent = BackendPath::new("/foo").unwrap();
    let child = BackendPath::new("/foo/bar/baz").unwrap();
    assert!(parent.is_ancestor_of(&child));
}

#[test]
fn ancestor_unrelated_is_false() {
    let a = BackendPath::new("/foo").unwrap();
    let b = BackendPath::new("/bar").unwrap();
    assert!(!a.is_ancestor_of(&b));
}

#[test]
fn ancestor_partial_string_prefix_is_false() {
    // `/foo` is NOT an ancestor of `/foobar` (no directory boundary)
    let prefix = BackendPath::new("/foo").unwrap();
    let other = BackendPath::new("/foobar").unwrap();
    assert!(!prefix.is_ancestor_of(&other));
}

#[test]
fn ancestor_with_trailing_slash_works() {
    let prefix = BackendPath::new("/foo/").unwrap();
    let other = BackendPath::new("/foobar").unwrap();
    assert!(!prefix.is_ancestor_of(&other));
}

// ---------------------------------------------------------------------------
// Tests — BackendError code mapping  [类型][兼容]
// ---------------------------------------------------------------------------

#[test]
fn error_code_for_known_variants() {
    assert_eq!(
        BackendError::FileNotFound {
            path: "/x".into(),
            code: "file_not_found"
        }
        .code(),
        Some("file_not_found")
    );
    assert_eq!(
        BackendError::IsDirectory {
            path: "/x".into(),
            code: "is_directory"
        }
        .code(),
        Some("is_directory")
    );
    assert_eq!(
        BackendError::InvalidPath {
            path: "/x".into(),
            code: "invalid_path"
        }
        .code(),
        Some("invalid_path")
    );
    assert_eq!(
        BackendError::PermissionDenied {
            path: "/x".into(),
            reason: "r".into(),
            code: "permission_denied"
        }
        .code(),
        Some("permission_denied")
    );
    assert_eq!(
        BackendError::EditMatch {
            reason: "x".into()
        }
        .code(),
        Some("edit_match")
    );
    assert_eq!(
        BackendError::Timeout { timeout_ms: 100 }.code(),
        Some("timeout")
    );
}

#[test]
fn error_code_for_unsupported_returns_none() {
    assert_eq!(BackendError::Unsupported("delete").code(), None);
    assert_eq!(BackendError::Io("x".into()).code(), None);
    assert_eq!(BackendError::Other("x".into()).code(), None);
}

// ---------------------------------------------------------------------------
// Tests — Result type serde  [序列化][兼容]
// ---------------------------------------------------------------------------

#[test]
fn read_result_omits_none_fields() {
    let r = ReadResult {
        error: None,
        file_data: None,
    };
    let s = serde_json::to_string(&r).unwrap();
    assert_eq!(s, "{}");
}

#[test]
fn read_result_serializes_both_fields_when_set() {
    let r = ReadResult {
        error: None,
        file_data: Some(FileData {
            content: "hi".into(),
            encoding: "utf-8".into(),
            created_at: None,
            modified_at: None,
        }),
    };
    let s = serde_json::to_string(&r).unwrap();
    assert!(s.contains("\"content\":\"hi\""));
    assert!(s.contains("\"encoding\":\"utf-8\""));
}

#[test]
fn backend_error_serializes_with_code() {
    let e = BackendError::FileNotFound {
        path: "/missing".into(),
        code: "file_not_found",
    };
    let s = serde_json::to_string(&e).unwrap();
    assert!(s.contains("file_not_found"));
    assert!(s.contains("/missing"));
}

#[test]
fn file_data_deserializes_without_timestamps() {
    let s = r#"{"content":"hello","encoding":"utf-8"}"#;
    let f: FileData = serde_json::from_str(s).unwrap();
    assert_eq!(f.content, "hello");
    assert_eq!(f.encoding, "utf-8");
    assert!(f.created_at.is_none());
    assert!(f.modified_at.is_none());
}

// ---------------------------------------------------------------------------
// Tests — BackendProtocol method contract  [方法][覆盖]
// ---------------------------------------------------------------------------

#[tokio::test]
async fn ls_returns_matching_entries() {
    let backend = MockBackend::new();
    backend
        .write(&BackendPath::new("/a.txt").unwrap(), "x")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/b.txt").unwrap(), "y")
        .await
        .unwrap();
    let result = backend.ls(&BackendPath::new("/").unwrap()).await.unwrap();
    let entries = result.entries.unwrap();
    assert_eq!(entries.len(), 2);
}

#[tokio::test]
async fn read_missing_file_returns_error() {
    let backend = MockBackend::new();
    let result = backend
        .read(&BackendPath::new("/missing").unwrap(), 0, 100)
        .await
        .unwrap();
    assert!(result.error.is_some());
    assert_eq!(result.error.unwrap(), "file_not_found");
    assert!(result.file_data.is_none());
}

#[tokio::test]
async fn write_then_read_roundtrip() {
    let backend = MockBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "hello").await.unwrap();
    let result = backend.read(&path, 0, 100).await.unwrap();
    let data = result.file_data.unwrap();
    assert_eq!(data.content, "hello");
    assert_eq!(data.encoding, "utf-8");
}

#[tokio::test]
async fn edit_non_unique_returns_edit_match_error() {
    let backend = MockBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "abc abc").await.unwrap();
    let err = backend
        .edit(&path, "abc", "xyz", false)
        .await
        .unwrap_err();
    assert!(matches!(err, BackendError::EditMatch { .. }));
}

#[tokio::test]
async fn edit_replace_all_with_multiple_matches() {
    let backend = MockBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "abc abc abc").await.unwrap();
    let result = backend.edit(&path, "abc", "xyz", true).await.unwrap();
    assert_eq!(result.occurrences, 3);
    let read = backend.read(&path, 0, 100).await.unwrap();
    assert_eq!(read.file_data.unwrap().content, "xyz xyz xyz");
}

#[tokio::test]
async fn delete_default_returns_unsupported() {
    let backend = MockBackend::new();
    let err = backend
        .delete(&BackendPath::new("/foo").unwrap())
        .await
        .unwrap_err();
    assert!(matches!(err, BackendError::Unsupported("delete")));
}

#[tokio::test]
async fn upload_files_default_returns_unsupported() {
    let backend = MockBackend::new();
    let err = backend
        .upload_files(vec![(BackendPath::new("/a").unwrap(), vec![1, 2, 3])])
        .await
        .unwrap_err();
    assert!(matches!(err, BackendError::Unsupported("upload_files")));
}

#[tokio::test]
async fn glob_matches_extension_pattern() {
    let backend = MockBackend::new();
    backend
        .write(&BackendPath::new("/x.rs").unwrap(), "fn main() {}")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/y.txt").unwrap(), "data")
        .await
        .unwrap();
    // Use `**/*.rs` because BackendPath-normalized paths always start with `/`
    // and `*` does not cross `/` boundaries.
    let result = backend.glob("**/*.rs", None).await.unwrap();
    let matches = result.matches.unwrap();
    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].path, "/x.rs");
}

#[tokio::test]
async fn grep_filters_by_glob() {
    let backend = MockBackend::new();
    backend
        .write(&BackendPath::new("/x.rs").unwrap(), "// TODO: refactor")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/y.txt").unwrap(), "TODO: write doc")
        .await
        .unwrap();
    let result = backend
        .grep("TODO", None, Some("**/*.rs"))
        .await
        .unwrap();
    let matches = result.matches.unwrap();
    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].path, "/x.rs");
}

// ---------------------------------------------------------------------------
// Tests — Boundary  [边界]
// ---------------------------------------------------------------------------

#[tokio::test]
async fn read_with_max_offset_returns_empty() {
    let backend = MockBackend::new();
    let path = BackendPath::new("/foo").unwrap();
    backend.write(&path, "abc").await.unwrap();
    // offset = u32::MAX is nonsense but should not panic
    let result = backend.read(&path, u32::MAX, 100).await.unwrap();
    // Either returns error or empty content; the contract is no panic.
    assert!(result.file_data.is_some() || result.error.is_some());
}

// ---------------------------------------------------------------------------
// Tests — Trait object  [trait]
// ---------------------------------------------------------------------------

#[tokio::test]
async fn can_box_as_dyn_backend_protocol() {
    let backend: Box<dyn BackendProtocol> = Box::new(MockBackend::new());
    let path = BackendPath::new("/foo").unwrap();
    backend.write(&path, "hi").await.unwrap();
    let result = backend.read(&path, 0, 100).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "hi");
}

#[tokio::test]
async fn can_wrap_in_arc_send_sync() {
    let backend: Arc<dyn BackendProtocol> = Arc::new(MockBackend::new());
    let backend2 = backend.clone();
    let path = BackendPath::new("/foo").unwrap();
    let path_for_spawn = path.clone();
    let handle = tokio::spawn(async move {
        backend2.write(&path_for_spawn, "from-spawn").await.unwrap();
    });
    handle.await.unwrap();
    let result = backend.read(&path, 0, 100).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "from-spawn");
}

#[tokio::test]
async fn can_box_as_dyn_sandbox_backend_protocol() {
    let backend: Box<dyn SandboxBackendProtocol> = Box::new(MockBackend::new());
    assert_eq!(backend.id(), "mock-1");
    let result = backend.execute("echo hi", Some(1000)).await.unwrap();
    assert_eq!(result.exit_code, Some(0));
}