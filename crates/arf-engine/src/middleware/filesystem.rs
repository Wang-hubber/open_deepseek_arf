//! `FilesystemMiddleware` — exposes BackendProtocol ops as tools (Phase 11 / 11.7).
//!
//! Wraps an `Arc<dyn BackendProtocol>` and, in `before_model_call`:
//! 1. Injects 6 tool specs (ls / read_file / write_file / edit_file / glob_files / grep_files)
//! 2. Appends a system message describing backend capabilities
//!
//! **Tool execution** is NOT done by this middleware — it only injects the
//! declarations. A separate `BackendToolNode` (Phase 11-M2 subsequent task)
//! handles `tool_exec` routing. Until then, the model can see the tools but
//! calls to them will fail at the Engine's tool registry.

use std::sync::Arc;

use arf_backend::{BackendError, BackendPath, BackendProtocol};
use arf_core::{Middleware, ModelRequest, State, ToolSpec};
use async_trait::async_trait;
use serde_json::{json, Value};

/// Names of the 6 standard filesystem tools injected by this middleware.
pub const FS_TOOL_LS: &str = "ls";
pub const FS_TOOL_READ: &str = "read_file";
pub const FS_TOOL_WRITE: &str = "write_file";
pub const FS_TOOL_EDIT: &str = "edit_file";
pub const FS_TOOL_GLOB: &str = "glob_files";
pub const FS_TOOL_GREP: &str = "grep_files";

/// Filesystem middleware — exposes backend ops as model tools.
pub struct FilesystemMiddleware {
    backend: Arc<dyn BackendProtocol>,
    /// Optional allow-list (tool name → keep). `None` means all 6 tools.
    allowed: Option<Vec<String>>,
    /// Optional deny-list (tool name → remove).
    denied: Vec<String>,
}

impl FilesystemMiddleware {
    /// Create with default tool set (all 6 tools).
    pub fn new(backend: Arc<dyn BackendProtocol>) -> Self {
        Self {
            backend,
            allowed: None,
            denied: vec![],
        }
    }

    /// Restrict to a specific allow-list of tool names.
    pub fn with_allowed(mut self, names: Vec<String>) -> Self {
        self.allowed = Some(names);
        self
    }

    /// Add a tool name to the deny list.
    pub fn deny(mut self, name: impl Into<String>) -> Self {
        self.denied.push(name.into());
        self
    }

    /// Borrow the wrapped backend.
    pub fn backend(&self) -> &Arc<dyn BackendProtocol> {
        &self.backend
    }

    /// Build the 6 standard tool specs.
    fn standard_tools(&self) -> Vec<ToolSpec> {
        let all = vec![
            (
                FS_TOOL_LS,
                "List files and directories at a backend path.",
                json!({
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Backend path to list, e.g. '/workspace'"
                        }
                    },
                    "required": ["path"]
                }),
            ),
            (
                FS_TOOL_READ,
                "Read file contents with optional line range.",
                json!({
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute backend path of the file"
                        },
                        "offset": {
                            "type": "integer",
                            "description": "0-indexed line offset (default 0)"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum lines to read (default 2000, 0 = all)"
                        }
                    },
                    "required": ["file_path"]
                }),
            ),
            (
                FS_TOOL_WRITE,
                "Write content to a file, creating or overwriting it.",
                json!({
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute backend path"
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to write"
                        }
                    },
                    "required": ["file_path", "content"]
                }),
            ),
            (
                FS_TOOL_EDIT,
                "Exact-string replacement in an existing file.",
                json!({
                    "type": "object",
                    "properties": {
                        "file_path": { "type": "string" },
                        "old_string": { "type": "string" },
                        "new_string": { "type": "string" },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace all occurrences (default false)"
                        }
                    },
                    "required": ["file_path", "old_string", "new_string"]
                }),
            ),
            (
                FS_TOOL_GLOB,
                "Find files matching a glob pattern.",
                json!({
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Glob pattern, e.g. '**/*.rs'"
                        },
                        "base": {
                            "type": "string",
                            "description": "Optional base directory"
                        }
                    },
                    "required": ["pattern"]
                }),
            ),
            (
                FS_TOOL_GREP,
                "Search for a literal text pattern in files.",
                json!({
                    "type": "object",
                    "properties": {
                        "pattern": { "type": "string" },
                        "path": { "type": "string" },
                        "glob_filter": { "type": "string" }
                    },
                    "required": ["pattern"]
                }),
            ),
        ];

        all.into_iter()
            .filter(|(name, _, _)| match &self.allowed {
                Some(allow) => allow.iter().any(|a| a == name),
                None => true,
            })
            .filter(|(name, _, _)| !self.denied.iter().any(|d| d == name))
            .map(|(name, desc, params)| ToolSpec::new(name, desc, params))
            .collect()
    }

    /// Build the system-prompt suffix describing backend capabilities.
    fn capabilities_text(&self) -> String {
        "\n\n# Filesystem Backend\n\n\
         You have access to a backend with these operations:\n\
         - `ls(path)`: list directory entries\n\
         - `read_file(path, offset?, limit?)`: read file content with line range\n\
         - `write_file(path, content)`: create or overwrite file\n\
         - `edit_file(path, old, new, replace_all?)`: exact-string replace\n\
         - `glob_files(pattern, base?)`: find files by glob pattern\n\
         - `grep_files(pattern, path?, glob_filter?)`: search file contents\n\n\
         All paths are absolute backend paths starting with `/`.\n\
         Use these tools to interact with the user's workspace."
            .to_string()
    }
}

#[async_trait]
impl Middleware for FilesystemMiddleware {
    fn name(&self) -> &str {
        "filesystem"
    }

    async fn before_agent(&self, _state: &State) {
        // One-shot setup: nothing to do here (backend already wrapped).
    }

    async fn before_model_call(&self, ctx: &mut ModelRequest, _state: &State) {
        // Inject the 6 standard tools (subject to allow/deny lists).
        for tool in self.standard_tools() {
            ctx.add_tool(tool);
        }
        // Append capabilities suffix.
        ctx.extend_system_suffix(self.capabilities_text());
    }
}

/// Parse a string from a tool call into a `BackendPath`, returning a
/// user-friendly error message if the path is malformed.
pub fn parse_backend_path(raw: &str) -> Result<BackendPath, String> {
    BackendPath::new(raw).map_err(|e| format!("{e}"))
}

/// Internal: helper for downstream `BackendToolExecutor`.
pub(crate) fn backend_error_to_json(e: BackendError) -> Value {
    json!({
        "error": e.to_string(),
        "code": e.code(),
    })
}