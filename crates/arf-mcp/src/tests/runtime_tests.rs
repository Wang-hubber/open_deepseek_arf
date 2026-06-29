use std::collections::HashMap;
use std::sync::Arc;

use serde_json::Value;

use crate::runtime::{LocalRuntime, RuntimeModule};
use crate::tool::Tool;
use crate::types::{ToolCallItem, ToolCallSet, ToolError};

struct EchoTool;
#[async_trait::async_trait]
impl Tool for EchoTool {
    fn name(&self) -> &str {
        "echo"
    }
    fn description(&self) -> &str {
        "Echo"
    }
    fn parameters_schema(&self) -> Value {
        serde_json::json!({"type": "object"})
    }
    async fn execute(&self, params: Value) -> Result<Value, ToolError> {
        Ok(params)
    }
}

struct FailTool;
#[async_trait::async_trait]
impl Tool for FailTool {
    fn name(&self) -> &str {
        "fail"
    }
    fn description(&self) -> &str {
        "Fails"
    }
    fn parameters_schema(&self) -> Value {
        Value::Null
    }
    async fn execute(&self, _params: Value) -> Result<Value, ToolError> {
        Err(ToolError::from("intentional failure"))
    }
}

// [方法] LocalRuntime capabilities 正确
#[test]
fn local_runtime_capabilities() {
    let rt = LocalRuntime;
    let caps = rt.capabilities();
    assert_eq!(caps["runtime"], "local");
    assert_eq!(caps["concurrency"], "layer-parallel");
}

// [方法] run_single 成功 → (success, result, None)
#[tokio::test]
async fn run_single_success() {
    let rt = LocalRuntime;
    let tool = EchoTool;
    let (status, result, error) = rt
        .run_single("c0", &tool, serde_json::json!({"x": 1}))
        .await;
    assert_eq!(status, "success");
    assert_eq!(result["x"], 1);
    assert!(error.is_none());
}

// [方法] run_single 失败 → (error, null, Some(message))
#[tokio::test]
async fn run_single_error() {
    let rt = LocalRuntime;
    let tool = FailTool;
    let (status, result, error) = rt
        .run_single("c0", &tool, serde_json::Value::Null)
        .await;
    assert_eq!(status, "error");
    assert_eq!(result, Value::Null);
    assert_eq!(error.as_deref(), Some("intentional failure"));
}

// [方法] execute 通过 DAG executor 运行单 tool
#[tokio::test]
async fn execute_via_dag_executor() {
    let rt = LocalRuntime;
    let mut tools = HashMap::new();
    tools.insert("echo".into(), Arc::new(EchoTool) as Arc<dyn Tool>);

    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![ToolCallItem {
            id: "c0".into(),
            tool: "echo".into(),
            params: serde_json::json!({"ok": true}),
            blocked_by: vec![],
            blocking: vec![],
        }],
        timeout_ms: None,
    };
    let result = rt.execute(&set, &tools).await;
    assert_eq!(result.results.len(), 1);
    assert_eq!(result.results[0].status, "success");
    assert_eq!(result.results[0].name, "echo");
}

// [类型] RuntimeModule trait 可用作 Box<dyn RuntimeModule>
#[test]
fn runtime_module_is_object_safe() {
    let rt: Box<dyn RuntimeModule> = Box::new(LocalRuntime);
    let caps = rt.capabilities();
    assert_eq!(caps["runtime"], "local");
}
