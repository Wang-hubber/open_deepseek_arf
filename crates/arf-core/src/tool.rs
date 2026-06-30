//! ToolSpec — describes a tool for LLM function-calling.
//!
//! Phase 6 task 6.1: tool definition type. Built up by ToolNode and
//! stamped into `ModelCall.tools` by Engine during ReAct loop.

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSpec {
    /// Tool name as LLM sees it (e.g., `"read_file"`).
    pub name: String,
    /// Human-readable description for the LLM.
    pub description: String,
    /// JSON Schema describing the tool's argument structure.
    pub parameters: Value,
}

impl ToolSpec {
    pub fn new(name: impl Into<String>, description: impl Into<String>, parameters: Value) -> Self {
        Self {
            name: name.into(),
            description: description.into(),
            parameters,
        }
    }
}
