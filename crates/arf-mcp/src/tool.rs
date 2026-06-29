use serde_json::Value;

use crate::types::ToolError;

/// A tool that MCP can execute.
///
/// Implementations are `Send + Sync` so they can be shared across tokio tasks.
/// Tool authors only need to implement `execute()` — error handling, panic
/// catching, and result packaging are centralized in the executor.
/// Sandboxing and approval are handled by separate Nodes on the Bus — not here.
#[async_trait::async_trait]
pub trait Tool: Send + Sync {
    /// Unique tool name: "read_file", "write_file", "search_content".
    fn name(&self) -> &str;

    /// Human-readable description for LLM function calling.
    fn description(&self) -> &str;

    /// JSON Schema for the tool's parameters.
    fn parameters_schema(&self) -> Value;

    /// Execute the tool with the given parameters.
    ///
    /// Returns `Ok(result)` on success, `Err(ToolError)` on failure.
    /// The executor also wraps this in `catch_unwind` for panic safety —
    /// tool authors can use plain `unwrap()` / `expect()` without breaking MCP.
    async fn execute(&self, params: Value) -> Result<Value, ToolError>;

    /// Cancel an in-progress execution.
    ///
    /// Called by the executor when:
    /// - A dependency fails (cascade cancel)
    /// - Engine sends a cancellation for this `tool_call_set`
    ///
    /// Default implementation is a no-op. Tools with long-running or
    /// resource-holding operations should override this to release
    /// resources and set a cancellation flag.
    async fn cancel(&self) {
        // no-op by default
    }
}
