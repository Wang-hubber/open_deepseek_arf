use crate::tool::Tool;
use crate::types::ToolError;
use serde_json::Value;

/// Mock Tool implementation for testing the Trait contract.
struct MockTool {
    name: String,
    description: String,
    schema: Value,
    result: Value,
}

impl MockTool {
    fn new(name: &str, result: Value) -> Self {
        Self {
            name: name.into(),
            description: format!("Mock tool: {name}"),
            schema: serde_json::json!({"type": "object"}),
            result,
        }
    }
}

#[async_trait::async_trait]
impl Tool for MockTool {
    fn name(&self) -> &str {
        &self.name
    }

    fn description(&self) -> &str {
        &self.description
    }

    fn parameters_schema(&self) -> Value {
        self.schema.clone()
    }

    async fn execute(&self, _params: Value) -> Result<Value, ToolError> {
        Ok(self.result.clone())
    }
}

// ═══════════════════════════════════════════════════════════════
// Tool trait — 8 tests
// ═══════════════════════════════════════════════════════════════

// [构造] MockTool 返回正确的 name、description、schema
#[test]
fn tool_name_description_schema() {
    let tool = MockTool::new("mock", serde_json::json!({"ok": true}));
    assert_eq!(tool.name(), "mock");
    assert_eq!(tool.description(), "Mock tool: mock");
    assert_eq!(
        tool.parameters_schema(),
        serde_json::json!({"type": "object"})
    );
}

// [方法] execute 返回预设结果
#[tokio::test]
async fn tool_execute_returns_result() {
    let tool = MockTool::new("mock", serde_json::json!({"ok": true}));
    let result = tool.execute(serde_json::json!({"x": 1})).await.unwrap();
    assert_eq!(result, serde_json::json!({"ok": true}));
}

// [方法] cancel 默认实现不 panic（no-op）
#[tokio::test]
async fn tool_cancel_default_is_noop() {
    let tool = MockTool::new("mock", serde_json::json!(null));
    tool.cancel().await; // should not panic
}

// [边界] name 为空字符串——不 panic
#[test]
fn tool_empty_name() {
    let tool = MockTool::new("", serde_json::json!(null));
    assert_eq!(tool.name(), "");
}

// [边界] 超大 JSON result（100KB）正常返回
#[tokio::test]
async fn tool_large_result() {
    let large = serde_json::Value::String("x".repeat(100_000));
    let tool = MockTool::new("big", large.clone());
    let result = tool.execute(serde_json::json!(null)).await.unwrap();
    assert_eq!(result, large);
}

// [类型] Tool trait 可用于 Arc<dyn Tool> 动态分发
#[test]
fn tool_trait_is_object_safe_via_async_trait() {
    let tool: std::sync::Arc<dyn Tool> =
        std::sync::Arc::new(MockTool::new("mock", serde_json::json!(null)));
    assert_eq!(tool.name(), "mock");
}

// [覆盖] execute 接收空 JSON 对象 {} 不报错
#[tokio::test]
async fn tool_execute_empty_params() {
    let tool = MockTool::new("mock", serde_json::json!({"ok": true}));
    let result = tool.execute(serde_json::json!({})).await.unwrap();
    assert_eq!(result, serde_json::json!({"ok": true}));
}

// [覆盖] execute 接收 null params 不报错
#[tokio::test]
async fn tool_execute_null_params() {
    let tool = MockTool::new("mock", serde_json::json!(42));
    let result = tool.execute(serde_json::Value::Null).await.unwrap();
    assert_eq!(result, serde_json::json!(42));
}
