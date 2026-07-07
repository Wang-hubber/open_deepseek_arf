//! Tests for `FilesystemMiddleware` (Phase 11 / 11.7).

use std::collections::HashMap;
use std::sync::Arc;

use arf_backend::{BackendPath, BackendProtocol, StateBackend};
use arf_core::{Middleware, ModelMessage, ModelRequest, State, ToolSpec};
use arf_engine::middleware::{
    parse_backend_path, FilesystemMiddleware, FS_TOOL_EDIT, FS_TOOL_GLOB, FS_TOOL_GREP, FS_TOOL_LS,
    FS_TOOL_READ, FS_TOOL_WRITE,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state_backend() -> Arc<dyn BackendProtocol> {
    Arc::new(StateBackend::new())
}

fn make_state_with_seed(seed: HashMap<BackendPath, String>) -> Arc<dyn BackendProtocol> {
    Arc::new(StateBackend::with_seed(seed))
}

// ---------------------------------------------------------------------------
// [构造] Construction
// ---------------------------------------------------------------------------

#[tokio::test]
async fn new_creates_with_default_six_tools() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    assert_eq!(mw.name(), "filesystem");
}

#[tokio::test]
async fn with_allowed_restricts_tool_set() {
    let mw = FilesystemMiddleware::new(make_state_backend())
        .with_allowed(vec![FS_TOOL_READ.to_string()]);
    // Trigger tool generation via before_model_call.
    let mut ctx = ModelRequest::new(vec![ModelMessage::new("user", "hi")], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    assert_eq!(ctx.tools.len(), 1);
    assert_eq!(ctx.tools[0].name, FS_TOOL_READ);
}

#[tokio::test]
async fn deny_removes_specific_tool() {
    let mw = FilesystemMiddleware::new(make_state_backend()).deny(FS_TOOL_WRITE);
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    let names: Vec<&str> = ctx.tools.iter().map(|t| t.name.as_str()).collect();
    assert_eq!(names.len(), 5);
    assert!(!names.contains(&FS_TOOL_WRITE));
    assert!(names.contains(&FS_TOOL_READ));
    assert!(names.contains(&FS_TOOL_LS));
}

#[tokio::test]
async fn allow_and_deny_compose() {
    let mw = FilesystemMiddleware::new(make_state_backend())
        .with_allowed(vec![
            FS_TOOL_READ.to_string(),
            FS_TOOL_WRITE.to_string(),
            FS_TOOL_EDIT.to_string(),
        ])
        .deny(FS_TOOL_WRITE);
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    let names: Vec<&str> = ctx.tools.iter().map(|t| t.name.as_str()).collect();
    assert_eq!(names.len(), 2);
    assert!(names.contains(&FS_TOOL_READ));
    assert!(names.contains(&FS_TOOL_EDIT));
    assert!(!names.contains(&FS_TOOL_WRITE));
}

#[tokio::test]
async fn empty_allowed_yields_no_tools() {
    let mw = FilesystemMiddleware::new(make_state_backend()).with_allowed(vec![]);
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    assert!(ctx.tools.is_empty());
}

// ---------------------------------------------------------------------------
// [方法] before_model_call behavior
// ---------------------------------------------------------------------------

#[tokio::test]
async fn before_model_call_injects_all_six_tools() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let mut ctx = ModelRequest::new(vec![ModelMessage::new("user", "hi")], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    assert_eq!(ctx.tools.len(), 6);
}

#[tokio::test]
async fn before_model_call_appends_system_suffix() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let mut ctx = ModelRequest::new(
        vec![ModelMessage::new("system", "You are a helpful assistant.")],
        vec![],
    );
    mw.before_model_call(&mut ctx, &State::default()).await;
    assert!(!ctx.system_prompt_suffix.is_empty());
    assert!(ctx.system_prompt_suffix.contains("Filesystem Backend"));
    assert!(ctx.system_prompt_suffix.contains("read_file"));
}

#[tokio::test]
async fn before_model_call_does_not_modify_existing_tools() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let existing = ToolSpec::new("existing_tool", "pre-existing", serde_json::json!({}));
    let mut ctx = ModelRequest::new(vec![], vec![existing.clone()]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    // Existing tool preserved + 6 new tools added.
    assert_eq!(ctx.tools.len(), 7);
    assert!(ctx.tools.iter().any(|t| t.name == "existing_tool"));
}

#[tokio::test]
async fn before_model_call_does_not_duplicate_tools() {
    // If the model already has a `read_file` tool, the middleware must not
    // inject another one (deduplication).
    let mw = FilesystemMiddleware::new(make_state_backend());
    let existing = ToolSpec::new(FS_TOOL_READ, "custom version", serde_json::json!({}));
    let mut ctx = ModelRequest::new(vec![], vec![existing]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    let read_count = ctx.tools.iter().filter(|t| t.name == FS_TOOL_READ).count();
    assert_eq!(read_count, 1);
}

// ---------------------------------------------------------------------------
// [工具] Schema correctness
// ---------------------------------------------------------------------------

#[tokio::test]
async fn ls_tool_has_path_parameter() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    let ls = ctx.tools.iter().find(|t| t.name == FS_TOOL_LS).unwrap();
    let schema = &ls.parameters;
    assert_eq!(schema["properties"]["path"]["type"], "string");
    let required = schema["required"].as_array().unwrap();
    assert!(required.iter().any(|v| v == "path"));
}

#[tokio::test]
async fn read_file_tool_has_offset_and_limit() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    let read = ctx.tools.iter().find(|t| t.name == FS_TOOL_READ).unwrap();
    let schema = &read.parameters;
    assert!(schema["properties"]["file_path"].is_object());
    assert!(schema["properties"]["offset"].is_object());
    assert!(schema["properties"]["limit"].is_object());
}

#[tokio::test]
async fn edit_file_tool_has_replace_all() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    let edit = ctx.tools.iter().find(|t| t.name == FS_TOOL_EDIT).unwrap();
    let schema = &edit.parameters;
    assert_eq!(schema["properties"]["replace_all"]["type"], "boolean");
}

#[tokio::test]
async fn glob_files_tool_has_pattern() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    let glob = ctx.tools.iter().find(|t| t.name == FS_TOOL_GLOB).unwrap();
    assert_eq!(glob.parameters["properties"]["pattern"]["type"], "string");
}

#[tokio::test]
async fn grep_files_tool_has_pattern() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    let grep = ctx.tools.iter().find(|t| t.name == FS_TOOL_GREP).unwrap();
    assert_eq!(grep.parameters["properties"]["pattern"]["type"], "string");
}

#[tokio::test]
async fn write_file_tool_requires_content() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let mut ctx = ModelRequest::new(vec![], vec![]);
    mw.before_model_call(&mut ctx, &State::default()).await;
    let write = ctx.tools.iter().find(|t| t.name == FS_TOOL_WRITE).unwrap();
    let required = write.parameters["required"].as_array().unwrap();
    assert!(required.iter().any(|v| v == "file_path"));
    assert!(required.iter().any(|v| v == "content"));
}

// ---------------------------------------------------------------------------
// [边界] parse_backend_path
// ---------------------------------------------------------------------------

#[test]
fn parse_backend_path_accepts_valid() {
    let result = parse_backend_path("/workspace/file.txt");
    assert!(result.is_ok());
}

#[test]
fn parse_backend_path_rejects_dotdot() {
    let result = parse_backend_path("/../etc/passwd");
    assert!(result.is_err());
    assert!(result.unwrap_err().contains("invalid path"));
}

#[test]
fn parse_backend_path_rejects_empty() {
    let result = parse_backend_path("");
    assert!(result.is_err());
}

// ---------------------------------------------------------------------------
// [trait] Backend access
// ---------------------------------------------------------------------------

#[tokio::test]
async fn backend_accessor_returns_arc() {
    let backend = make_state_backend();
    let mw = FilesystemMiddleware::new(backend.clone());
    let retrieved = mw.backend();
    // Same Arc (pointer equality).
    assert!(Arc::ptr_eq(retrieved, &backend));
}

#[tokio::test]
async fn can_be_arc_dyn_middleware() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    let _arc_mw: Arc<dyn arf_core::Middleware> = Arc::new(mw);
}

#[tokio::test]
async fn before_agent_is_no_op() {
    let mw = FilesystemMiddleware::new(make_state_backend());
    // Should not panic.
    mw.before_agent(&State::default()).await;
}

// ---------------------------------------------------------------------------
// [集成] End-to-end with seeded backend
// ---------------------------------------------------------------------------

#[tokio::test]
async fn middleware_works_with_seeded_state_backend() {
    let mut seed = HashMap::new();
    seed.insert(
        BackendPath::new("/a.txt").unwrap(),
        "alpha content".into(),
    );
    let backend = make_state_with_seed(seed);
    let mw = FilesystemMiddleware::new(backend);
    let mut ctx = ModelRequest::new(
        vec![ModelMessage::new("user", "list files at /")],
        vec![],
    );
    mw.before_model_call(&mut ctx, &State::default()).await;
    // Middleware registered the tools; backend is the underlying storage.
    assert_eq!(ctx.tools.len(), 6);
    assert!(!ctx.system_prompt_suffix.is_empty());
}