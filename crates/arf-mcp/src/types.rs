use serde::{Deserialize, Serialize};

// ── ToolError ─────────────────────────────────────────────────────────

/// Error returned by a tool's `execute()`.
///
/// The executor catches this and packages it into the `ToolResultItem`.
/// Tool authors don't need to follow any error convention — just return `Err`.
#[derive(Debug, Clone)]
pub struct ToolError {
    pub message: String,
}

impl std::fmt::Display for ToolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for ToolError {}

impl From<&str> for ToolError {
    fn from(s: &str) -> Self {
        Self {
            message: s.to_string(),
        }
    }
}

impl From<String> for ToolError {
    fn from(s: String) -> Self {
        Self { message: s }
    }
}

// ── ToolCallItem ──────────────────────────────────────────────────────

/// A single tool invocation within a `tool_call_set`.
///
/// Bidirectional dependency lock (same pattern as task State):
///   `blocked_by` — who blocks me (I depend on them)
///   `blocking`  — who I block (they depend on me)
///
/// Engine sets the dependencies; executor derives the reverse edges
/// during DAG construction for efficient bidirectional traversal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallItem {
    /// Unique ID within this `tool_call_set` (e.g., "call_0", "call_1").
    pub id: String,
    /// Tool name to invoke.
    pub tool: String,
    /// Parameters for the tool call.
    pub params: serde_json::Value,
    /// IDs of calls that must complete before this one.
    /// Empty = no dependencies, can execute immediately.
    #[serde(default)]
    pub blocked_by: Vec<String>,
    /// IDs of calls that depend on this one.
    /// Empty = nothing waits for this call.
    #[serde(default)]
    pub blocking: Vec<String>,
}

// ── ToolCallSet ───────────────────────────────────────────────────────

/// A set of 1-N tool calls dispatched together.
///
/// Engine assembles this after receiving a model_response with tool_calls.
/// MCP builds a DAG from the bidirectional lock (`blocked_by` / `blocking`),
/// topologically sorts, and executes:
/// - Calls without dependencies: concurrent execution
/// - Calls with `blocked_by`: serialized by dependency order
/// - Any call fails → cascade cancel along `blocking` (forward)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallSet {
    /// The session this tool call set belongs to.
    pub session_id: String,
    /// 1-N tool calls in this set.
    pub calls: Vec<ToolCallItem>,
    /// Per-call timeout in milliseconds. `None` = no timeout (Engine's choice).
    /// MCP does not impose a default or hidden deadline.
    #[serde(default)]
    pub timeout_ms: Option<u64>,
}

// ── ToolResultItem ─────────────────────────────────────────────────────

/// Result of a single tool call.
///
/// All error packaging is centralized in the executor — tool authors
/// never construct this struct directly. The executor:
/// 1. Calls `tool.execute()` inside `catch_unwind`
/// 2. On `Ok(val)` → `status: "success"`, `result: val`
/// 3. On `Err(e)` → `status: "error"`, `error: e.message`
/// 4. On panic → `status: "error"`, `error: "panic: {message}"`
/// 5. On cancel (cascade or timeout) → calls `tool.cancel()`, `status: "cancelled"`
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResultItem {
    /// Matches `ToolCallItem.id`.
    pub call_id: String,
    /// Tool name — matches `ToolCallItem.tool`. Executor backfills from the
    /// original request. Carried so ModelAdapter (Phase 5, arf-model-adapter)
    /// can construct the tool-result message without a call_id→name lookup.
    pub name: String,
    /// `"success"` or `"error"` or `"cancelled"`.
    pub status: String,
    /// The tool's return value. Null on error/cancelled.
    pub result: serde_json::Value,
    /// Error message populated by executor on error/cancelled.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

// ── ToolResultSet ──────────────────────────────────────────────────────

/// Aggregated results for a `tool_call_set`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResultSet {
    /// The session this result set belongs to.
    pub session_id: String,
    /// Results, one per call in the original `ToolCallSet`.
    pub results: Vec<ToolResultItem>,
}
