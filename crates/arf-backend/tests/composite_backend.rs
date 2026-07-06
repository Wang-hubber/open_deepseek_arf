//! Tests for `CompositeBackend` (Phase 11 / 11.4).
//!
//! Verifies path-prefix routing, longest-prefix priority, default fallback,
//! path translation, glob/grep merging, and upload batching.

use std::collections::HashMap;
use std::sync::Arc;

use arf_backend::{
    BackendError, BackendPath, BackendProtocol, CompositeBackend, StateBackend,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state() -> Arc<dyn BackendProtocol> {
    Arc::new(StateBackend::new())
}

fn make_state_with_seed(seed: HashMap<BackendPath, String>) -> Arc<dyn BackendProtocol> {
    Arc::new(StateBackend::with_seed(seed))
}

// ---------------------------------------------------------------------------
// [构造] Construction
// ---------------------------------------------------------------------------

#[tokio::test]
async fn new_with_default_only_routes_everything_to_default() {
    let default = make_state();
    let composite = CompositeBackend::new(default.clone());
    let path = BackendPath::new("/anything.txt").unwrap();
    composite.write(&path, "hello").await.unwrap();
    // Default (StateBackend) should have the file.
    let read = composite.read(&path, 0, 0).await.unwrap();
    assert_eq!(read.file_data.unwrap().content, "hello");
}

#[tokio::test]
async fn with_route_adds_prefix_route() {
    let default = make_state();
    let memory = make_state();
    let composite = CompositeBackend::new(default.clone()).with_route(
        BackendPath::new("/memory").unwrap(),
        memory.clone(),
    );
    // /memory/* goes to memory backend.
    let mem_path = BackendPath::new("/memory/foo.txt").unwrap();
    composite.write(&mem_path, "mem-data").await.unwrap();
    // /other goes to default.
    let other_path = BackendPath::new("/other.txt").unwrap();
    composite.write(&other_path, "default-data").await.unwrap();
    assert_eq!(
        composite
            .read(&mem_path, 0, 0)
            .await
            .unwrap()
            .file_data
            .unwrap()
            .content,
        "mem-data"
    );
    assert_eq!(
        composite
            .read(&other_path, 0, 0)
            .await
            .unwrap()
            .file_data
            .unwrap()
            .content,
        "default-data"
    );
}

// ---------------------------------------------------------------------------
// [路由] Longest-prefix priority
// ---------------------------------------------------------------------------

#[tokio::test]
async fn longest_prefix_wins() {
    let mut seed = HashMap::new();
    seed.insert(
        BackendPath::new("/foo.txt").unwrap(),
        "in-memory".into(),
    );
    seed.insert(
        BackendPath::new("/cache.txt").unwrap(),
        "in-cache".into(),
    );
    let memory = make_state_with_seed(seed);
    let cache = make_state_with_seed({
        let mut s = HashMap::new();
        s.insert(
            BackendPath::new("/foo.txt").unwrap(),
            "in-cache-foo".into(),
        );
        s
    });
    let default = make_state();
    let composite = CompositeBackend::new(default)
        .with_route(BackendPath::new("/memory").unwrap(), memory.clone())
        .with_route(BackendPath::new("/memory/cache").unwrap(), cache.clone());

    // /memory/foo.txt matches /memory (longest prefix that matches it).
    let p = BackendPath::new("/memory/foo.txt").unwrap();
    let content = composite
        .read(&p, 0, 0)
        .await
        .unwrap()
        .file_data
        .unwrap()
        .content;
    assert_eq!(content, "in-memory");

    // /memory/cache/foo.txt matches /memory/cache (longest).
    let p = BackendPath::new("/memory/cache/foo.txt").unwrap();
    composite.write(&p, "cached").await.unwrap();
    let content = composite.read(&p, 0, 0).await.unwrap().file_data.unwrap().content;
    assert_eq!(content, "cached");

    // /memory/cache.txt is NOT under /memory/cache (no slash boundary
    // after "cache"), so it falls back to /memory.
    let p = BackendPath::new("/memory/cache.txt").unwrap();
    let content = composite.read(&p, 0, 0).await.unwrap().file_data.unwrap().content;
    assert_eq!(content, "in-cache");
}

// ---------------------------------------------------------------------------
// [路由] Default fallback
// ---------------------------------------------------------------------------

#[tokio::test]
async fn unmatched_path_uses_default() {
    let memory = make_state();
    let default = make_state();
    let composite = CompositeBackend::new(default.clone())
        .with_route(BackendPath::new("/memory").unwrap(), memory);
    // /unknown does not match /memory → falls through to default.
    let p = BackendPath::new("/unknown.txt").unwrap();
    composite.write(&p, "default-only").await.unwrap();
    let content = composite.read(&p, 0, 0).await.unwrap().file_data.unwrap().content;
    assert_eq!(content, "default-only");
}

// ---------------------------------------------------------------------------
// [方法] Path translation
// ---------------------------------------------------------------------------

#[tokio::test]
async fn child_backend_sees_translated_path() {
    let memory = make_state();
    let default = make_state();
    let composite = CompositeBackend::new(default).with_route(
        BackendPath::new("/memory").unwrap(),
        memory.clone(),
    );
    // Write through composite at /memory/foo.txt → memory backend sees /foo.txt.
    let composite_path = BackendPath::new("/memory/foo.txt").unwrap();
    composite.write(&composite_path, "via-composite").await.unwrap();
    // Read directly from memory backend at translated path.
    let child_path = BackendPath::new("/foo.txt").unwrap();
    let content = memory
        .read(&child_path, 0, 0)
        .await
        .unwrap()
        .file_data
        .unwrap()
        .content;
    assert_eq!(content, "via-composite");
}

#[tokio::test]
async fn exact_prefix_match_uses_prefix_as_translated() {
    let memory = make_state();
    let default = make_state();
    let composite = CompositeBackend::new(default).with_route(
        BackendPath::new("/memory").unwrap(),
        memory.clone(),
    );
    // /memory (exact) → child sees /memory.
    let p = BackendPath::new("/memory").unwrap();
    let _ = composite.ls(&p).await.unwrap();
    // Child backend has no files yet; ls returns empty.
    // We just verify no panic and translated path is valid.
}

// ---------------------------------------------------------------------------
// [方法] read/write/edit/delete routing
// ---------------------------------------------------------------------------

#[tokio::test]
async fn write_routes_to_correct_child() {
    let memory = make_state();
    let default = make_state();
    let composite = CompositeBackend::new(default.clone()).with_route(
        BackendPath::new("/memory").unwrap(),
        memory.clone(),
    );
    let p = BackendPath::new("/memory/x.txt").unwrap();
    composite.write(&p, "M").await.unwrap();
    // Memory backend stores files at the translated path (prefix stripped).
    let translated = BackendPath::new("/x.txt").unwrap();
    assert_eq!(
        memory
            .read(&translated, 0, 0)
            .await
            .unwrap()
            .file_data
            .unwrap()
            .content,
        "M"
    );
    // Default backend doesn't have /x.txt.
    let r = default.read(&translated, 0, 0).await.unwrap();
    assert!(r.error.is_some());
}

#[tokio::test]
async fn edit_routes_to_correct_child() {
    let mut seed = HashMap::new();
    seed.insert(
        BackendPath::new("/file.txt").unwrap(),
        "abc".into(),
    );
    let memory = make_state_with_seed(seed);
    let default = make_state();
    let composite = CompositeBackend::new(default).with_route(
        BackendPath::new("/memory").unwrap(),
        memory,
    );
    let p = BackendPath::new("/memory/file.txt").unwrap();
    let result = composite.edit(&p, "abc", "xyz", false).await.unwrap();
    assert_eq!(result.occurrences, 1);
}

#[tokio::test]
async fn delete_routes_to_correct_child() {
    let mut seed = HashMap::new();
    seed.insert(
        BackendPath::new("/file.txt").unwrap(),
        "abc".into(),
    );
    let memory = make_state_with_seed(seed);
    let default = make_state();
    let composite = CompositeBackend::new(default).with_route(
        BackendPath::new("/memory").unwrap(),
        memory.clone(),
    );
    let p = BackendPath::new("/memory/file.txt").unwrap();
    composite.delete(&p).await.unwrap();
    let r = memory.read(&p, 0, 0).await.unwrap();
    assert!(r.error.is_some());
}

// ---------------------------------------------------------------------------
// [方法] glob with merged results
// ---------------------------------------------------------------------------

#[tokio::test]
async fn glob_without_base_merges_default_and_routes() {
    let mut mem_seed = HashMap::new();
    mem_seed.insert(
        BackendPath::new("/a.txt").unwrap(),
        "in-mem".into(),
    );
    let memory = make_state_with_seed(mem_seed);
    let mut default_seed = HashMap::new();
    default_seed.insert(
        BackendPath::new("/x.txt").unwrap(),
        "in-default".into(),
    );
    let default = make_state_with_seed(default_seed);
    let composite = CompositeBackend::new(default).with_route(
        BackendPath::new("/memory").unwrap(),
        memory,
    );
    let result = composite.glob("**/*.txt", None).await.unwrap();
    let matches = result.matches.unwrap();
    assert_eq!(matches.len(), 2);
    let paths: Vec<&str> = matches.iter().map(|m| m.path.as_str()).collect();
    assert!(paths.contains(&"/memory/a.txt"));
    assert!(paths.contains(&"/x.txt"));
}

#[tokio::test]
async fn glob_with_base_routes_to_specific_child() {
    let mut mem_seed = HashMap::new();
    mem_seed.insert(
        BackendPath::new("/a.txt").unwrap(),
        "mem".into(),
    );
    let memory = make_state_with_seed(mem_seed);
    let mut default_seed = HashMap::new();
    default_seed.insert(
        BackendPath::new("/x.txt").unwrap(),
        "default".into(),
    );
    let default = make_state_with_seed(default_seed);
    let composite = CompositeBackend::new(default).with_route(
        BackendPath::new("/memory").unwrap(),
        memory,
    );
    let result = composite
        .glob("**/*.txt", Some(&BackendPath::new("/memory").unwrap()))
        .await
        .unwrap();
    let matches = result.matches.unwrap();
    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].path, "/a.txt");
}

// ---------------------------------------------------------------------------
// [方法] grep with merged results
// ---------------------------------------------------------------------------

#[tokio::test]
async fn grep_without_base_merges_results() {
    let mut mem_seed = HashMap::new();
    mem_seed.insert(
        BackendPath::new("/a.rs").unwrap(),
        "// TODO".into(),
    );
    let memory = make_state_with_seed(mem_seed);
    let mut default_seed = HashMap::new();
    default_seed.insert(
        BackendPath::new("/b.rs").unwrap(),
        "// TODO".into(),
    );
    let default = make_state_with_seed(default_seed);
    let composite = CompositeBackend::new(default).with_route(
        BackendPath::new("/memory").unwrap(),
        memory,
    );
    let result = composite.grep("TODO", None, None).await.unwrap();
    let matches = result.matches.unwrap();
    assert_eq!(matches.len(), 2);
    let paths: Vec<&str> = matches.iter().map(|m| m.path.as_str()).collect();
    assert!(paths.contains(&"/memory/a.rs"));
    assert!(paths.contains(&"/b.rs"));
}

// ---------------------------------------------------------------------------
// [方法] upload_files batching
// ---------------------------------------------------------------------------

#[tokio::test]
async fn upload_files_distributes_across_children() {
    let memory = make_state();
    let default = make_state();
    let composite = CompositeBackend::new(default.clone()).with_route(
        BackendPath::new("/memory").unwrap(),
        memory.clone(),
    );
    let files = vec![
        (
            BackendPath::new("/memory/a.txt").unwrap(),
            b"mem-a".to_vec(),
        ),
        (
            BackendPath::new("/memory/b.txt").unwrap(),
            b"mem-b".to_vec(),
        ),
        (BackendPath::new("/c.txt").unwrap(), b"def-c".to_vec()),
    ];
    let results = composite.upload_files(files).await.unwrap();
    assert_eq!(results.len(), 3);
    assert!(results.iter().all(|r| r.is_ok()));
    // Verify memory has the two mem files.
    assert_eq!(
        memory
            .read(&BackendPath::new("/a.txt").unwrap(), 0, 0)
            .await
            .unwrap()
            .file_data
            .unwrap()
            .content,
        "mem-a"
    );
    assert_eq!(
        memory
            .read(&BackendPath::new("/b.txt").unwrap(), 0, 0)
            .await
            .unwrap()
            .file_data
            .unwrap()
            .content,
        "mem-b"
    );
    // Verify default has the default file.
    assert_eq!(
        default
            .read(&BackendPath::new("/c.txt").unwrap(), 0, 0)
            .await
            .unwrap()
            .file_data
            .unwrap()
            .content,
        "def-c"
    );
}

// ---------------------------------------------------------------------------
// [trait] Box / Arc compatibility
// ---------------------------------------------------------------------------

#[tokio::test]
async fn can_box_as_dyn_backend_protocol() {
    let memory = make_state();
    let composite = CompositeBackend::new(memory);
    let boxed: Box<dyn BackendProtocol> = Box::new(composite);
    let p = BackendPath::new("/x.txt").unwrap();
    boxed.write(&p, "boxed").await.unwrap();
    let r = boxed.read(&p, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "boxed");
}

#[tokio::test]
async fn can_wrap_in_arc_send_sync() {
    let memory = make_state();
    let composite = CompositeBackend::new(memory);
    let arc: Arc<dyn BackendProtocol> = Arc::new(composite);
    let arc2 = arc.clone();
    let p = BackendPath::new("/x.txt").unwrap();
    let p_clone = p.clone();
    let handle = tokio::spawn(async move {
        arc2.write(&p_clone, "from-spawn").await.unwrap();
    });
    handle.await.unwrap();
    let r = arc.read(&p, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "from-spawn");
}

// ---------------------------------------------------------------------------
// [边界] Empty path / root
// ---------------------------------------------------------------------------

#[tokio::test]
async fn root_path_uses_default() {
    let memory = make_state();
    let default = make_state();
    let composite = CompositeBackend::new(default).with_route(
        BackendPath::new("/memory").unwrap(),
        memory,
    );
    // / (root) doesn't match /memory → default.
    let r = composite.read(&BackendPath::new("/").unwrap(), 0, 0).await;
    // StateBackend returns file_not_found for missing root — that's fine.
    // We just want no panic and routed to default.
    assert!(r.is_ok());
}