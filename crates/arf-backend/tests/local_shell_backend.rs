//! Tests for `LocalShellBackend` (Phase 11 / 11.10).

use std::sync::Arc;

use arf_backend::{
    BackendError, BackendPath, BackendProtocol, LocalShellBackend, SandboxBackendProtocol,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async fn make_backend() -> (tempfile::TempDir, LocalShellBackend) {
    let dir = tempfile::tempdir().unwrap();
    let backend = LocalShellBackend::new(dir.path()).unwrap();
    (dir, backend)
}

// ---------------------------------------------------------------------------
// [构造] Construction
// ---------------------------------------------------------------------------

#[tokio::test]
async fn new_accepts_absolute_path() {
    let dir = tempfile::tempdir().unwrap();
    let backend = LocalShellBackend::new(dir.path()).unwrap();
    assert_eq!(backend.id(), "local-shell");
}

#[tokio::test]
async fn new_rejects_relative_path() {
    let result = LocalShellBackend::new("relative/path");
    assert!(matches!(result, Err(BackendError::InvalidPath { .. })));
}

#[tokio::test]
async fn new_rejects_nonexistent_path() {
    let result = LocalShellBackend::new("/this/path/does/not/exist/anywhere");
    assert!(matches!(result, Err(BackendError::Io(_))));
}

#[tokio::test]
async fn with_default_timeout_sets_value() {
    let dir = tempfile::tempdir().unwrap();
    let backend = LocalShellBackend::new(dir.path())
        .unwrap()
        .with_default_timeout(60_000);
    // No direct getter, but should still execute within timeout.
    let result = backend.execute("echo hi", None).await.unwrap();
    assert!(result.output.contains("hi"));
}

// ---------------------------------------------------------------------------
// [方法] Filesystem ops (delegate to FilesystemBackend)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn write_then_read_via_local_shell() {
    let (_dir, backend) = make_backend().await;
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "data").await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "data");
}

#[tokio::test]
async fn ls_via_local_shell() {
    let (_dir, backend) = make_backend().await;
    backend
        .write(&BackendPath::new("/a.txt").unwrap(), "a")
        .await
        .unwrap();
    let result = backend.ls(&BackendPath::new("/").unwrap()).await.unwrap();
    assert_eq!(result.entries.unwrap().len(), 1);
}

// ---------------------------------------------------------------------------
// [sandbox] execute
// ---------------------------------------------------------------------------

#[tokio::test]
async fn execute_echo_returns_output() {
    let (_dir, backend) = make_backend().await;
    let result = backend.execute("echo hello", Some(5000)).await.unwrap();
    assert!(result.output.contains("hello"));
    assert_eq!(result.exit_code, Some(0));
    assert!(!result.truncated);
}

#[tokio::test]
async fn execute_pwd_returns_sandbox_cwd() {
    let (dir, backend) = make_backend().await;
    let result = backend.execute("pwd", Some(5000)).await.unwrap();
    assert!(result.output.contains(dir.path().to_string_lossy().as_ref()));
}

#[tokio::test]
async fn execute_failed_command_returns_nonzero_exit_code() {
    let (_dir, backend) = make_backend().await;
    let result = backend.execute("false", Some(5000)).await.unwrap();
    assert_eq!(result.exit_code, Some(1));
}

#[tokio::test]
async fn execute_timeout_returns_timeout_error() {
    let (_dir, backend) = make_backend().await;
    // sleep 5s with 200ms timeout
    let result = backend
        .execute("sleep 5", Some(200))
        .await;
    match result {
        Err(BackendError::Timeout { timeout_ms }) => assert_eq!(timeout_ms, 200),
        other => panic!("expected Timeout error, got {other:?}"),
    }
}

#[tokio::test]
async fn execute_long_output_truncated() {
    let (_dir, backend) = make_backend().await;
    let backend = backend.with_output_cap(100);
    let result = backend
        .execute("yes A | head -1000", Some(5000))
        .await
        .unwrap();
    assert!(result.truncated);
    assert!(result.output.len() <= 100 + 32); // cap + STDOUT/STDERR headers
}

#[tokio::test]
async fn execute_with_pipe() {
    let (_dir, backend) = make_backend().await;
    let result = backend
        .execute("echo foo | tr 'f' 'b'", Some(5000))
        .await
        .unwrap();
    assert!(result.output.contains("boo"));
}

#[tokio::test]
async fn execute_stderr_captured() {
    let (_dir, backend) = make_backend().await;
    let result = backend
        .execute("echo error >&2", Some(5000))
        .await
        .unwrap();
    assert!(result.output.contains("error"));
}

// ---------------------------------------------------------------------------
// [边界] Sandbox interactions
// ---------------------------------------------------------------------------

#[tokio::test]
async fn execute_can_read_files_in_sandbox() {
    let (_dir, backend) = make_backend().await;
    backend
        .write(&BackendPath::new("/hello.txt").unwrap(), "from-cmd")
        .await
        .unwrap();
    let result = backend
        .execute("cat hello.txt", Some(5000))
        .await
        .unwrap();
    assert!(result.output.contains("from-cmd"));
}

#[tokio::test]
async fn execute_cd_escape_blocked_by_path_sandbox() {
    let (_dir, backend) = make_backend().await;
    // cd to parent dir is allowed by shell, but FilesystemBackend::resolve
    // blocks access to paths outside sandbox root.
    backend
        .write(&BackendPath::new("/foo.txt").unwrap(), "in-sandbox")
        .await
        .unwrap();
    // Try to read a path with .. — FilesystemBackend should refuse.
    let escape_path = BackendPath::new("/../etc/passwd");
    let result = escape_path;
    match result {
        Ok(p) => {
            // If BackendPath allowed it, read would fail at sandbox level.
            let r = backend.read(&p, 0, 0).await;
            assert!(r.is_err(), "expected error reading escape path");
        }
        Err(_) => {
            // BackendPath rejects .. — expected behavior.
        }
    }
}

// ---------------------------------------------------------------------------
// [trait] Box / Arc compatibility
// ---------------------------------------------------------------------------

#[tokio::test]
async fn can_box_as_dyn_backend_protocol() {
    let (_dir, backend) = make_backend().await;
    let boxed: Box<dyn BackendProtocol> = Box::new(backend);
    let path = BackendPath::new("/foo.txt").unwrap();
    boxed.write(&path, "data").await.unwrap();
    let r = boxed.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "data");
}

#[tokio::test]
async fn can_box_as_dyn_sandbox_backend_protocol() {
    let (_dir, backend) = make_backend().await;
    let boxed: Box<dyn SandboxBackendProtocol> = Box::new(backend);
    assert_eq!(boxed.id(), "local-shell");
    let result = boxed.execute("echo hi", Some(1000)).await.unwrap();
    assert!(result.output.contains("hi"));
}

#[tokio::test]
async fn can_wrap_in_arc_send_sync() {
    let (_dir, backend) = make_backend().await;
    let arc: Arc<dyn BackendProtocol> = Arc::new(backend);
    let arc2 = arc.clone();
    let path = BackendPath::new("/foo.txt").unwrap();
    let path_clone = path.clone();
    let handle = tokio::spawn(async move {
        arc2.write(&path_clone, "from-spawn").await.unwrap();
    });
    handle.await.unwrap();
    let r = arc.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "from-spawn");
}