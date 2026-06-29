use std::collections::HashMap;
use std::sync::Arc;

use serde_json::Value;

use crate::executor;
use crate::tool::Tool;
use crate::types::{ToolCallSet, ToolResultSet};

/// Execution backend for tool calls — bound at LocalMcpNode construction.
///
/// The RuntimeModule trait decouples DAG scheduling from subprocess execution.
/// The executor handles topology, concurrency, and cascade cancel — it calls
/// `run_single()` for each individual tool invocation (via `execute()`).
///
/// Implementations:
/// - `LocalRuntime` (framework default): spawn subprocesses on the host
/// - `SandboxRuntime` (user-defined, future): forward to a sandbox Bus node
#[async_trait::async_trait]
pub trait RuntimeModule: Send + Sync {
    /// Self-describing capabilities — injected into `node_online.payload.capabilities`.
    ///
    /// Engine sees this and knows the execution environment without understanding
    /// sandbox internals.
    fn capabilities(&self) -> Value;

    /// Execute a full `tool_call_set` via the DAG executor.
    ///
    /// Default implementation delegates to `executor::execute()`.
    async fn execute(
        &self,
        call_set: &ToolCallSet,
        tools: &HashMap<String, Arc<dyn Tool>>,
    ) -> ToolResultSet {
        executor::execute(call_set, tools).await
    }

    /// Execute a single tool call. Used by tests and simple execution paths.
    async fn run_single(
        &self,
        _call_id: &str,
        tool: &dyn Tool,
        params: Value,
    ) -> (String, Value, Option<String>) {
        match tool.execute(params).await {
            Ok(val) => ("success".into(), val, None),
            Err(e) => ("error".into(), Value::Null, Some(e.message)),
        }
    }
}

// ── LocalRuntime (default) ──────────────────────────────────────────

/// Default RuntimeModule — spawns subprocesses directly on the host.
pub struct LocalRuntime;

#[async_trait::async_trait]
impl RuntimeModule for LocalRuntime {
    fn capabilities(&self) -> Value {
        serde_json::json!({"runtime": "local", "concurrency": "layer-parallel"})
    }
}

// ── RemoteRuntime ──────────────────────────────────────────────────

/// Remote RuntimeModule — tools are HttpProxyTool instances.
/// Uses the default execute() which delegates to executor (DAG-compatible).
pub struct RemoteRuntime;

#[async_trait::async_trait]
impl RuntimeModule for RemoteRuntime {
    fn capabilities(&self) -> Value {
        serde_json::json!({"runtime": "remote"})
    }
}
