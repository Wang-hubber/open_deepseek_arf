//! Tests for `FilesystemBackend` (Phase 11 / 11.3).
//!
//! Uses `tempfile::TempDir` for sandbox roots. Covers real-FS ops,
//! path-sandbox security, glob, grep, and concurrent writes.

use std::sync::Arc;

use arf_backend::{BackendError, BackendPath, BackendProtocol, FilesystemBackend};
use tempfile::TempDir;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async fn make_backend() -> (TempDir, FilesystemBackend) {
    let dir = tempfile::tempdir().expect("create temp dir");
    let backend = FilesystemBackend::new(dir.path()).expect("create backend");
    (dir, backend)
}

// ---------------------------------------------------------------------------
// [构造] Construction
// ---------------------------------------------------------------------------

#[tokio::test]
async fn new_accepts_absolute_path() {
    let dir = tempfile::tempdir().unwrap();
    let backend = FilesystemBackend::new(dir.path()).unwrap();
    assert_eq!(backend.root(), dir.path());
}

#[tokio::test]
async fn new_rejects_relative_path() {
    let result = FilesystemBackend::new("relative/path");
    assert!(matches!(result, Err(BackendError::InvalidPath { .. })));
}

#[tokio::test]
async fn new_rejects_nonexistent_path() {
    let result = FilesystemBackend::new("/this/path/does/not/exist/anywhere");
    assert!(matches!(result, Err(BackendError::Io(_))));
}

#[tokio::test]
async fn new_rejects_file_as_root() {
    let dir = tempfile::tempdir().unwrap();
    let file_path = dir.path().join("a-file.txt");
    std::fs::write(&file_path, "hi").unwrap();
    let result = FilesystemBackend::new(&file_path);
    assert!(matches!(result, Err(BackendError::Io(_))));
}

// ---------------------------------------------------------------------------
// [方法] write / read roundtrip on real FS
// ---------------------------------------------------------------------------

#[tokio::test]
async fn write_then_read_roundtrip() {
    let (_dir, backend) = make_backend().await;
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "hello world").await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "hello world");
}

#[tokio::test]
async fn write_creates_parent_directories() {
    let (_dir, backend) = make_backend().await;
    let path = BackendPath::new("/a/b/c/file.txt").unwrap();
    backend.write(&path, "nested").await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "nested");
}

#[tokio::test]
async fn read_missing_file_returns_error() {
    let (_dir, backend) = make_backend().await;
    let result = backend
        .read(&BackendPath::new("/missing.txt").unwrap(), 0, 0)
        .await
        .unwrap();
    assert_eq!(result.error.unwrap(), "file_not_found");
}

#[tokio::test]
async fn write_overwrites_existing() {
    let (_dir, backend) = make_backend().await;
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "v1").await.unwrap();
    backend.write(&path, "v2").await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "v2");
}

// ---------------------------------------------------------------------------
// [方法] edit
// ---------------------------------------------------------------------------

#[tokio::test]
async fn edit_unique_match() {
    let (_dir, backend) = make_backend().await;
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "hello world").await.unwrap();
    let result = backend
        .edit(&path, "world", "rust", false)
        .await
        .unwrap();
    assert_eq!(result.occurrences, 1);
    let read = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(read.file_data.unwrap().content, "hello rust");
}

#[tokio::test]
async fn edit_non_unique_match_fails() {
    let (_dir, backend) = make_backend().await;
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "abc abc").await.unwrap();
    let err = backend.edit(&path, "abc", "xyz", false).await.unwrap_err();
    assert!(matches!(err, BackendError::EditMatch { .. }));
}

#[tokio::test]
async fn edit_replace_all() {
    let (_dir, backend) = make_backend().await;
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "abc abc abc").await.unwrap();
    let result = backend.edit(&path, "abc", "xyz", true).await.unwrap();
    assert_eq!(result.occurrences, 3);
}

// ---------------------------------------------------------------------------
// [方法] ls / delete
// ---------------------------------------------------------------------------

#[tokio::test]
async fn ls_returns_entries() {
    let (_dir, backend) = make_backend().await;
    backend
        .write(&BackendPath::new("/a.txt").unwrap(), "a")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/b.txt").unwrap(), "b")
        .await
        .unwrap();
    let result = backend
        .ls(&BackendPath::new("/").unwrap())
        .await
        .unwrap();
    let entries = result.entries.unwrap();
    assert_eq!(entries.len(), 2);
}

#[tokio::test]
async fn ls_file_returns_not_a_directory() {
    let (_dir, backend) = make_backend().await;
    backend
        .write(&BackendPath::new("/foo.txt").unwrap(), "x")
        .await
        .unwrap();
    let err = backend
        .ls(&BackendPath::new("/foo.txt").unwrap())
        .await
        .unwrap_err();
    assert!(matches!(err, BackendError::NotADirectory { .. }));
}

#[tokio::test]
async fn delete_file_removes_it() {
    let (dir, backend) = make_backend().await;
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "x").await.unwrap();
    backend.delete(&path).await.unwrap();
    assert!(!dir.path().join("foo.txt").exists());
}

#[tokio::test]
async fn delete_directory_recursively() {
    let (dir, backend) = make_backend().await;
    backend
        .write(&BackendPath::new("/sub/a.txt").unwrap(), "a")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/sub/b.txt").unwrap(), "b")
        .await
        .unwrap();
    backend
        .delete(&BackendPath::new("/sub").unwrap())
        .await
        .unwrap();
    assert!(!dir.path().join("sub").exists());
}

// ---------------------------------------------------------------------------
// [方法] glob / grep
// ---------------------------------------------------------------------------

#[tokio::test]
async fn glob_matches_files() {
    let (_dir, backend) = make_backend().await;
    backend
        .write(&BackendPath::new("/x.rs").unwrap(), "fn main() {}")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/y.txt").unwrap(), "data")
        .await
        .unwrap();
    let result = backend.glob("**/*.rs", None).await.unwrap();
    let matches = result.matches.unwrap();
    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].path, "/x.rs");
}

#[tokio::test]
async fn grep_finds_matching_lines() {
    let (_dir, backend) = make_backend().await;
    backend
        .write(&BackendPath::new("/a.txt").unwrap(), "hello\nTODO: fix\nbye")
        .await
        .unwrap();
    let result = backend.grep("TODO", None, None).await.unwrap();
    let matches = result.matches.unwrap();
    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].line, 2);
    assert_eq!(matches[0].text, "TODO: fix");
}

// ---------------------------------------------------------------------------
// [边界] Path sandbox security
//
// BackendPath::new() rejects `..` upfront (so `/../etc/passwd` never
// reaches the backend — it fails with InvalidPath at construction).
// The runtime sandbox protects against symlinks pointing outside root.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn backend_path_rejects_dotdot_at_construction() {
    // BackendPath normalizes `..` → InvalidPath at construction.
    let result = BackendPath::new("/../etc/passwd");
    assert!(matches!(result, Err(BackendError::InvalidPath { .. })));
    let result = BackendPath::new("/foo/../bar");
    assert!(matches!(result, Err(BackendError::InvalidPath { .. })));
}

#[tokio::test]
async fn sandbox_rejects_symlink_pointing_outside() {
    let outside_dir = tempfile::tempdir().unwrap();
    let outside_file = outside_dir.path().join("secret.txt");
    std::fs::write(&outside_file, "SECRET").unwrap();
    let sandbox_dir = tempfile::tempdir().unwrap();
    let link_path = sandbox_dir.path().join("evil_link");
    std::os::unix::fs::symlink(&outside_file, &link_path).unwrap();
    let backend = FilesystemBackend::new(sandbox_dir.path()).unwrap();
    let path = BackendPath::new("/evil_link").unwrap();
    // Symlink resolves to outside_file → canonicalize finds it outside root → PermissionDenied.
    let err = backend.read(&path, 0, 0).await.unwrap_err();
    assert!(matches!(err, BackendError::PermissionDenied { .. }));
}

#[tokio::test]
async fn sandbox_rejects_write_to_dotdot_path() {
    let (_dir, backend) = make_backend().await;
    let path_result = BackendPath::new("/../foo.txt");
    // BackendPath rejects `..` so write can never be reached with an escape path.
    assert!(matches!(path_result, Err(BackendError::InvalidPath { .. })));
}

// ---------------------------------------------------------------------------
// [边界] Binary file
// ---------------------------------------------------------------------------

#[tokio::test]
async fn read_binary_file_returns_error_string() {
    let (dir, backend) = make_backend().await;
    let bin_path = dir.path().join("binary.bin");
    std::fs::write(&bin_path, [0xFF, 0xFE, 0xFD, 0xFC]).unwrap();
    let path = BackendPath::new("/binary.bin").unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert!(result.error.is_some());
    assert!(result.file_data.is_none());
}

// ---------------------------------------------------------------------------
// [并发] Concurrent writes
// ---------------------------------------------------------------------------

#[tokio::test]
async fn concurrent_writes_to_different_files() {
    let (_dir, backend) = make_backend().await;
    let backend = Arc::new(backend);
    let mut handles = Vec::new();
    for i in 0..20 {
        let backend = backend.clone();
        handles.push(tokio::spawn(async move {
            let path = BackendPath::new(&format!("/file_{i:03}.txt")).unwrap();
            backend.write(&path, &format!("content_{i}")).await.unwrap();
        }));
    }
    for h in handles {
        h.await.unwrap();
    }
    let result = backend.ls(&BackendPath::new("/").unwrap()).await.unwrap();
    assert_eq!(result.entries.unwrap().len(), 20);
}

// ---------------------------------------------------------------------------
// [trait] Box / Arc compatibility
// ---------------------------------------------------------------------------

#[tokio::test]
async fn can_box_as_dyn_backend_protocol() {
    let (_dir, backend) = make_backend().await;
    let backend: Box<dyn BackendProtocol> = Box::new(backend);
    let path = BackendPath::new("/foo").unwrap();
    backend.write(&path, "hi").await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "hi");
}

#[tokio::test]
async fn can_wrap_in_arc_send_sync() {
    let (_dir, backend) = make_backend().await;
    let backend: Arc<dyn BackendProtocol> = Arc::new(backend);
    let backend2 = backend.clone();
    let path = BackendPath::new("/foo").unwrap();
    let path_for_spawn = path.clone();
    let handle = tokio::spawn(async move {
        backend2.write(&path_for_spawn, "from-spawn").await.unwrap();
    });
    handle.await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "from-spawn");
}

// ---------------------------------------------------------------------------
// [方法] upload_files batch
// ---------------------------------------------------------------------------

#[tokio::test]
async fn upload_files_writes_all() {
    let (_dir, backend) = make_backend().await;
    let files = vec![
        (BackendPath::new("/a.txt").unwrap(), b"alpha".to_vec()),
        (
            BackendPath::new("/sub/b.txt").unwrap(),
            b"beta".to_vec(),
        ),
    ];
    let results = backend.upload_files(files).await.unwrap();
    assert!(results.iter().all(|r| r.is_ok()));
    assert_eq!(
        backend
            .read(&BackendPath::new("/sub/b.txt").unwrap(), 0, 0)
            .await
            .unwrap()
            .file_data
            .unwrap()
            .content,
        "beta"
    );
}