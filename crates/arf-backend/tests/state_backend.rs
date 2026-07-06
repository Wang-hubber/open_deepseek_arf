//! Tests for `StateBackend` (Phase 11 / 11.2).
//!
//! Covers BackendProtocol methods, SandboxBackendProtocol defaults, and
//! concurrency. Annotated per ARF testing convention:
//! [构造][方法][边界][trait][sandbox][并发][时间]

use std::collections::HashMap;
use std::sync::Arc;

use arf_backend::{
    BackendError, BackendPath, BackendProtocol, DEFAULT_STATE_BACKEND_ID, SandboxBackendProtocol,
    StateBackend,
};

// ---------------------------------------------------------------------------
// [构造] Construction
// ---------------------------------------------------------------------------

#[tokio::test]
async fn new_creates_empty_backend() {
    let backend = StateBackend::new();
    assert_eq!(backend.len(), 0);
    assert!(backend.is_empty());
    assert_eq!(backend.id(), DEFAULT_STATE_BACKEND_ID);
}

#[tokio::test]
async fn with_seed_populates_files() {
    let mut seed = HashMap::new();
    seed.insert(BackendPath::new("/a.txt").unwrap(), "alpha".into());
    seed.insert(BackendPath::new("/b.txt").unwrap(), "beta".into());
    let backend = StateBackend::with_seed(seed);
    assert_eq!(backend.len(), 2);
    let read = backend
        .read(&BackendPath::new("/a.txt").unwrap(), 0, 0)
        .await
        .unwrap();
    assert_eq!(read.file_data.unwrap().content, "alpha");
}

#[tokio::test]
async fn with_id_overrides_default_id() {
    let backend = StateBackend::with_id("session-42");
    assert_eq!(backend.id(), "session-42");
}

#[tokio::test]
async fn default_trait_returns_empty_backend() {
    let backend: StateBackend = Default::default();
    assert!(backend.is_empty());
}

// ---------------------------------------------------------------------------
// [方法] write / read roundtrip
// ---------------------------------------------------------------------------

#[tokio::test]
async fn write_then_read_roundtrip() {
    let backend = StateBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "hello world").await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    let data = result.file_data.unwrap();
    assert_eq!(data.content, "hello world");
    assert_eq!(data.encoding, "utf-8");
}

#[tokio::test]
async fn read_missing_file_returns_error_string() {
    let backend = StateBackend::new();
    let result = backend
        .read(&BackendPath::new("/missing").unwrap(), 0, 0)
        .await
        .unwrap();
    assert!(result.error.is_some());
    assert_eq!(result.error.unwrap(), "file_not_found");
    assert!(result.file_data.is_none());
}

#[tokio::test]
async fn write_overwrites_existing_file() {
    let backend = StateBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "v1").await.unwrap();
    backend.write(&path, "v2").await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "v2");
}

// ---------------------------------------------------------------------------
// [方法] edit (unique / non-unique / not-found / replace_all)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn edit_unique_match_succeeds() {
    let backend = StateBackend::new();
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
    let backend = StateBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "abc abc").await.unwrap();
    let err = backend.edit(&path, "abc", "xyz", false).await.unwrap_err();
    assert!(matches!(err, BackendError::EditMatch { .. }));
}

#[tokio::test]
async fn edit_replace_all_with_multiple_matches() {
    let backend = StateBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "abc abc abc").await.unwrap();
    let result = backend.edit(&path, "abc", "xyz", true).await.unwrap();
    assert_eq!(result.occurrences, 3);
    let read = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(read.file_data.unwrap().content, "xyz xyz xyz");
}

#[tokio::test]
async fn edit_missing_file_returns_file_not_found() {
    let backend = StateBackend::new();
    let err = backend
        .edit(
            &BackendPath::new("/missing").unwrap(),
            "x",
            "y",
            false,
        )
        .await
        .unwrap_err();
    assert!(matches!(err, BackendError::FileNotFound { .. }));
}

// ---------------------------------------------------------------------------
// [方法] delete (single / recursive / missing)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn delete_single_file_removes_it() {
    let backend = StateBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "x").await.unwrap();
    backend.delete(&path).await.unwrap();
    assert_eq!(backend.len(), 0);
}

#[tokio::test]
async fn delete_directory_recursively_removes_children() {
    let backend = StateBackend::new();
    backend
        .write(&BackendPath::new("/dir/a.txt").unwrap(), "a")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/dir/sub/b.txt").unwrap(), "b")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/dir/sub/c.txt").unwrap(), "c")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/other.txt").unwrap(), "x")
        .await
        .unwrap();
    assert_eq!(backend.len(), 4);
    backend
        .delete(&BackendPath::new("/dir").unwrap())
        .await
        .unwrap();
    assert_eq!(backend.len(), 1);
}

#[tokio::test]
async fn delete_missing_path_returns_file_not_found() {
    let backend = StateBackend::new();
    let err = backend
        .delete(&BackendPath::new("/missing").unwrap())
        .await
        .unwrap_err();
    assert!(matches!(err, BackendError::FileNotFound { .. }));
}

// ---------------------------------------------------------------------------
// [方法] ls (boundary matching)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn ls_returns_all_files_at_prefix() {
    let backend = StateBackend::new();
    backend
        .write(&BackendPath::new("/dir/a.txt").unwrap(), "a")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/dir/b.txt").unwrap(), "b")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/other.txt").unwrap(), "x")
        .await
        .unwrap();
    let result = backend
        .ls(&BackendPath::new("/dir").unwrap())
        .await
        .unwrap();
    let entries = result.entries.unwrap();
    assert_eq!(entries.len(), 2);
    let paths: Vec<&str> = entries.iter().map(|e| e.path.as_str()).collect();
    assert!(paths.contains(&"/dir/a.txt"));
    assert!(paths.contains(&"/dir/b.txt"));
}

#[tokio::test]
async fn ls_does_not_cross_directory_boundary() {
    let backend = StateBackend::new();
    // `/foo` is a directory ancestor of `/foo/x.txt` (slash boundary)
    // but NOT of `/foobar.txt` (no slash boundary).
    backend
        .write(&BackendPath::new("/foo/x.txt").unwrap(), "x")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/foobar.txt").unwrap(), "x")
        .await
        .unwrap();
    let result = backend
        .ls(&BackendPath::new("/foo").unwrap())
        .await
        .unwrap();
    let entries = result.entries.unwrap();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].path, "/foo/x.txt");
}

// ---------------------------------------------------------------------------
// [方法] glob / grep
// ---------------------------------------------------------------------------

#[tokio::test]
async fn glob_matches_extension_pattern() {
    let backend = StateBackend::new();
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
async fn grep_finds_matching_lines_with_glob_filter() {
    let backend = StateBackend::new();
    backend
        .write(&BackendPath::new("/a.rs").unwrap(), "// TODO: refactor")
        .await
        .unwrap();
    backend
        .write(&BackendPath::new("/b.txt").unwrap(), "TODO: write doc")
        .await
        .unwrap();
    let result = backend
        .grep("TODO", None, Some("**/*.rs"))
        .await
        .unwrap();
    let matches = result.matches.unwrap();
    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].path, "/a.rs");
    assert_eq!(matches[0].line, 1);
}

// ---------------------------------------------------------------------------
// [边界] read with offset/limit
// ---------------------------------------------------------------------------

#[tokio::test]
async fn read_offset_and_limit_slicing() {
    let backend = StateBackend::new();
    let path = BackendPath::new("/multi.txt").unwrap();
    backend
        .write(&path, "line1\nline2\nline3\nline4\nline5")
        .await
        .unwrap();
    // Read lines 2..4 (offset=1, limit=2 → lines[1..3] = "line2\nline3")
    let result = backend.read(&path, 1, 2).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "line2\nline3");
}

#[tokio::test]
async fn read_max_offset_does_not_panic() {
    let backend = StateBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "abc").await.unwrap();
    let result = backend.read(&path, u32::MAX, 0).await.unwrap();
    // Either empty content or error; contract is no panic.
    assert!(result.file_data.is_some() || result.error.is_some());
    if let Some(data) = result.file_data {
        assert!(data.content.is_empty());
    }
}

#[tokio::test]
async fn read_limit_zero_returns_all_lines() {
    let backend = StateBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "a\nb\nc").await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(result.file_data.unwrap().content, "a\nb\nc");
}

// ---------------------------------------------------------------------------
// [方法] upload_files (batch)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn upload_files_batch_succeeds_for_utf8() {
    let backend = StateBackend::new();
    let files = vec![
        (BackendPath::new("/a.txt").unwrap(), b"alpha".to_vec()),
        (BackendPath::new("/b.txt").unwrap(), b"beta".to_vec()),
    ];
    let results = backend.upload_files(files).await.unwrap();
    assert_eq!(results.len(), 2);
    assert!(results.iter().all(|r| r.is_ok()));
    assert_eq!(backend.len(), 2);
}

#[tokio::test]
async fn upload_files_rejects_non_utf8_with_per_item_error() {
    let backend = StateBackend::new();
    let files = vec![
        (BackendPath::new("/good.txt").unwrap(), b"ok".to_vec()),
        (
            BackendPath::new("/bad.bin").unwrap(),
            vec![0xFF, 0xFE, 0xFD],
        ),
    ];
    let results = backend.upload_files(files).await.unwrap();
    assert_eq!(results.len(), 2);
    assert!(results[0].is_ok());
    assert!(results[1].is_err());
    assert_eq!(backend.len(), 1);
}

// ---------------------------------------------------------------------------
// [trait] Trait object compatibility
// ---------------------------------------------------------------------------

#[tokio::test]
async fn can_box_as_dyn_backend_protocol() {
    let backend: Box<dyn BackendProtocol> = Box::new(StateBackend::new());
    let path = BackendPath::new("/foo").unwrap();
    backend.write(&path, "hi").await.unwrap();
    let read = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(read.file_data.unwrap().content, "hi");
}

#[tokio::test]
async fn can_wrap_in_arc_send_sync() {
    let backend: Arc<dyn BackendProtocol> = Arc::new(StateBackend::new());
    let backend2 = backend.clone();
    let path = BackendPath::new("/foo").unwrap();
    let path_for_spawn = path.clone();
    let handle = tokio::spawn(async move {
        backend2.write(&path_for_spawn, "from-spawn").await.unwrap();
    });
    handle.await.unwrap();
    let read = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(read.file_data.unwrap().content, "from-spawn");
}

#[tokio::test]
async fn can_box_as_dyn_sandbox_backend_protocol() {
    let backend: Box<dyn SandboxBackendProtocol> = Box::new(StateBackend::new());
    assert_eq!(backend.id(), DEFAULT_STATE_BACKEND_ID);
}

// ---------------------------------------------------------------------------
// [sandbox] StateBackend is safe default — rejects execute
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sandbox_execute_is_rejected() {
    let backend = StateBackend::new();
    let err = backend.execute("echo hello", Some(1000)).await.unwrap_err();
    assert!(matches!(err, BackendError::Unsupported("execute")));
}

#[tokio::test]
async fn sandbox_id_with_custom_value() {
    let backend = StateBackend::with_id("session-7");
    assert_eq!(backend.id(), "session-7");
    let err = backend.execute("rm -rf /", None).await.unwrap_err();
    assert!(matches!(err, BackendError::Unsupported("execute")));
}

// ---------------------------------------------------------------------------
// [并发] Concurrent operations
// ---------------------------------------------------------------------------

#[tokio::test]
async fn concurrent_writes_do_not_lose_data() {
    let backend = Arc::new(StateBackend::new());
    let mut handles = Vec::new();
    for i in 0..100 {
        let backend = backend.clone();
        handles.push(tokio::spawn(async move {
            let path = BackendPath::new(&format!("/file_{i:03}.txt")).unwrap();
            backend.write(&path, &format!("content_{i}")).await.unwrap();
        }));
    }
    for h in handles {
        h.await.unwrap();
    }
    assert_eq!(backend.len(), 100);
}

#[tokio::test]
async fn concurrent_reads_see_latest_write() {
    let backend = Arc::new(StateBackend::new());
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "v1").await.unwrap();

    let mut handles = Vec::new();
    for _ in 0..10 {
        let backend = backend.clone();
        let path = path.clone();
        handles.push(tokio::spawn(async move {
            // Each task reads concurrently with the others; they should
            // all see *some* version of the file (either v1 or v2).
            let result = backend.read(&path, 0, 0).await.unwrap();
            let content = result.file_data.unwrap().content;
            assert!(content == "v1" || content == "v2");
        }));
    }
    backend.write(&path, "v2").await.unwrap();
    for h in handles {
        h.await.unwrap();
    }
}

// ---------------------------------------------------------------------------
// [边界] Large content
// ---------------------------------------------------------------------------

#[tokio::test]
async fn write_large_content_succeeds() {
    let backend = StateBackend::new();
    let path = BackendPath::new("/big.txt").unwrap();
    let content = "x".repeat(1_000_000); // 1MB
    backend.write(&path, &content).await.unwrap();
    let result = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(result.file_data.unwrap().content.len(), 1_000_000);
}

// ---------------------------------------------------------------------------
// [时间] Modified timestamp updates
// ---------------------------------------------------------------------------

#[tokio::test]
async fn write_updates_modified_at() {
    let backend = StateBackend::new();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "v1").await.unwrap();
    let first_modified = backend
        .read(&path, 0, 0)
        .await
        .unwrap()
        .file_data
        .unwrap()
        .modified_at;
    assert!(first_modified.is_some());
    // Sleep briefly to ensure timestamp differs.
    tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    backend.write(&path, "v2").await.unwrap();
    let second_modified = backend
        .read(&path, 0, 0)
        .await
        .unwrap()
        .file_data
        .unwrap()
        .modified_at
        .unwrap();
    assert!(second_modified > first_modified.unwrap());
}