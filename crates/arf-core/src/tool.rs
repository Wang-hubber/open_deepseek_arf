//! ToolSpec — describes a tool for LLM function-calling.
//!
//! Phase 6 task 6.1: tool definition type. Built up by ToolNode and
//! stamped into `ModelCall.tools` by Engine during ReAct loop.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Permission level controlling Engine's runtime gating of tool calls
/// (Phase 9 F-017). The default `Allow` preserves legacy behaviour.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ToolPermission {
    /// Tool runs without asking the user.
    Allow,
    /// Tool must ask the user before running — Engine sends a
    /// `permission_request` message and waits for `permission_response`.
    Ask,
    /// Tool is blocked — Engine rejects the call with a `tool_result` error.
    Deny,
}

impl Default for ToolPermission {
    fn default() -> Self {
        ToolPermission::Allow
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSpec {
    /// Tool name as LLM sees it (e.g., `"read_file"`).
    pub name: String,
    /// Human-readable description for the LLM.
    pub description: String,
    /// JSON Schema describing the tool's argument structure.
    pub parameters: Value,
    /// Permission level (Phase 9 F-017). Defaults to `Allow` so legacy
    /// callers continue to work.
    #[serde(default)]
    pub permission: ToolPermission,
}

impl ToolSpec {
    pub fn new(name: impl Into<String>, description: impl Into<String>, parameters: Value) -> Self {
        Self {
            name: name.into(),
            description: description.into(),
            parameters,
            permission: ToolPermission::default(),
        }
    }
}
