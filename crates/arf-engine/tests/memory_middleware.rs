//! Tests for `MemoryMiddleware` (Phase 11 / 11.9).

use std::collections::HashMap;
use std::sync::Arc;

use arf_backend::{BackendPath, BackendProtocol, StateBackend};
use arf_core::{Middleware, ModelMessage, ModelRequest, State};
use arf_engine::middleware::{MemoryMiddleware, DEFAULT_MEMORY_PATH, MEMORY_HEADER};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_backend_with_agents_md(content: &str) -> Arc<dyn BackendProtocol> {
    let mut seed = HashMap::new();
    seed.insert(
        BackendPath::new(DEFAULT_MEMORY_PATH).unwrap(),
        content.to_string(),
    );
    Arc::new(StateBackend::with_seed(seed))
}

fn make_empty_backend() -> Arc<dyn BackendProtocol> {
    Arc::new(StateBackend::new())
}

// ---------------------------------------------------------------------------
// [构造] Construction
// ---------------------------------------------------------------------------

#[test]
fn new_uses_default_path() {
    let mw = MemoryMiddleware::new(make_empty_backend());
    assert_eq!(mw.name(), "memory");
}

#[test]
fn with_memory_path_sets_custom_path() {
    let mw = MemoryMiddleware::new(make_empty_backend())
        .with_memory_path(BackendPath::new("/CUSTOM.md").unwrap());
    // Just verify it doesn't panic; path is internal.
    assert_eq!(mw.name(), "memory");
}

#[test]
fn with_header_sets_custom_header() {
    let mw = MemoryMiddleware::new(make_empty_backend()).with_header("Custom Header:");
    assert_eq!(mw.name(), "memory");
}

#[test]
fn warn_on_missing_does_not_panic() {
    let _mw = MemoryMiddleware::new(make_empty_backend()).warn_on_missing();
}

// ---------------------------------------------------------------------------
// [方法] load_memory
// ---------------------------------------------------------------------------

#[tokio::test]
async fn load_memory_returns_some_when_file_exists() {
    let backend = make_backend_with_agents_md("# Project Rules\n- Always run tests.");
    let mw = MemoryMiddleware::new(backend);
    let result = mw.load_memory().await.unwrap();
    assert!(result.is_some());
    assert!(result.unwrap().contains("Project Rules"));
}

#[tokio::test]
async fn load_memory_returns_none_when_file_missing() {
    let mw = MemoryMiddleware::new(make_empty_backend());
    let result = mw.load_memory().await.unwrap();
    assert!(result.is_none());
}

#[tokio::test]
async fn load_memory_with_custom_path() {
    let mut seed = HashMap::new();
    seed.insert(
        BackendPath::new("/custom/path.md").unwrap(),
        "custom content".into(),
    );
    let backend = Arc::new(StateBackend::with_seed(seed));
    let mw = MemoryMiddleware::new(backend)
        .with_memory_path(BackendPath::new("/custom/path.md").unwrap());
    let result = mw.load_memory().await.unwrap();
    assert_eq!(result.unwrap(), "custom content");
}

// ---------------------------------------------------------------------------
// [方法] before_model_call
// ---------------------------------------------------------------------------

#[tokio::test]
async fn before_model_call_injects_memory_into_suffix() {
    let backend = make_backend_with_agents_md("# Memory\n- rule 1");
    let mw = MemoryMiddleware::new(backend);
    let mut ctx = ModelRequest::new(vec![ModelMessage::new("user", "hi")], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    assert!(!ctx.system_prompt_suffix.is_empty());
    assert!(ctx.system_prompt_suffix.contains("rule 1"));
}

#[tokio::test]
async fn before_model_call_uses_default_header() {
    let backend = make_backend_with_agents_md("content");
    let mw = MemoryMiddleware::new(backend);
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    assert!(ctx.system_prompt_suffix.contains(MEMORY_HEADER.trim()));
}

#[tokio::test]
async fn before_model_call_uses_custom_header() {
    let backend = make_backend_with_agents_md("content");
    let mw = MemoryMiddleware::new(backend).with_header(">>> AGENT MEMORY <<<\n");
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    assert!(ctx.system_prompt_suffix.contains(">>> AGENT MEMORY <<<"));
    assert!(!ctx.system_prompt_suffix.contains("AGENTS.md (Persistent Memory)"));
}

#[tokio::test]
async fn before_model_call_no_op_when_memory_missing() {
    let mw = MemoryMiddleware::new(make_empty_backend());
    let mut ctx = ModelRequest::new(vec![ModelMessage::new("user", "hi")], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    // Empty backend → no memory → suffix stays empty.
    assert!(ctx.system_prompt_suffix.is_empty());
}

// ---------------------------------------------------------------------------
// [trait] Hook behavior
// ---------------------------------------------------------------------------

#[tokio::test]
async fn before_agent_does_not_panic() {
    let backend = make_backend_with_agents_md("# rules");
    let mw = MemoryMiddleware::new(backend);
    mw.before_agent(&State::default()).await;
}

#[tokio::test]
async fn before_agent_with_missing_file_does_not_panic() {
    let mw = MemoryMiddleware::new(make_empty_backend());
    mw.before_agent(&State::default()).await;
}

#[tokio::test]
async fn after_agent_is_no_op() {
    let backend = make_backend_with_agents_md("# rules");
    let mw = MemoryMiddleware::new(backend);
    mw.after_agent(&State::default(), "final output").await;
}

// ---------------------------------------------------------------------------
// [trait] Box / Arc compatibility
// ---------------------------------------------------------------------------

#[tokio::test]
async fn can_be_arc_dyn_middleware() {
    let mw = MemoryMiddleware::new(make_empty_backend());
    let arc: Arc<dyn arf_core::Middleware> = Arc::new(mw);
    assert_eq!(arc.name(), "memory");
}

#[tokio::test]
async fn can_be_box_dyn_middleware() {
    let mw = MemoryMiddleware::new(make_empty_backend());
    let boxed: Box<dyn arf_core::Middleware> = Box::new(mw);
    assert_eq!(boxed.name(), "memory");
}

// ---------------------------------------------------------------------------
// [边界] Edge cases
// ---------------------------------------------------------------------------

#[tokio::test]
async fn empty_memory_file_yields_empty_string() {
    let backend = make_backend_with_agents_md("");
    let mw = MemoryMiddleware::new(backend);
    let result = mw.load_memory().await.unwrap();
    assert_eq!(result.unwrap(), "");
}

#[tokio::test]
async fn large_memory_file_loaded_fully() {
    let large = "x".repeat(100_000);
    let backend = make_backend_with_agents_md(&large);
    let mw = MemoryMiddleware::new(backend);
    let result = mw.load_memory().await.unwrap();
    assert_eq!(result.unwrap().len(), 100_000);
}

#[tokio::test]
async fn backend_accessor_returns_arc() {
    let backend = make_empty_backend();
    let mw = MemoryMiddleware::new(backend.clone());
    let retrieved = mw.backend();
    assert!(Arc::ptr_eq(retrieved, &backend));
}

// ---------------------------------------------------------------------------
// [集成] End-to-end with middleware chain
// ---------------------------------------------------------------------------

#[tokio::test]
async fn chained_with_filesystem_middleware() {
    use arf_engine::middleware::FilesystemMiddleware;

    let backend = make_backend_with_agents_md("# Chained memory rules");
    let fs_mw = FilesystemMiddleware::new(backend.clone());
    let mem_mw = MemoryMiddleware::new(backend);

    let mut ctx = ModelRequest::new(vec![ModelMessage::new("user", "hi")], vec![]);

    // Run filesystem middleware first (adds 6 tools + capabilities suffix).
    fs_mw.before_model_call(&mut ctx, &State::default()).await;
    // Then memory middleware (adds AGENTS.md content).
    mem_mw.before_model_call(&mut ctx, &State::default()).await;

    // Verify both contributed.
    assert_eq!(ctx.tools.len(), 6);
    assert!(ctx.system_prompt_suffix.contains("Filesystem Backend"));
    assert!(ctx.system_prompt_suffix.contains("Chained memory rules"));
}