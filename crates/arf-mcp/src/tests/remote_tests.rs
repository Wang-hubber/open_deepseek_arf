use crate::remote::{CallToolResult, JsonRpcError, RemoteToolDef, ToolContent};

// ═══════════════════════════════════════════════════════════════
// RemoteToolDef — 2 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn remote_tool_def_deserialize() {
    let json = r#"{"name":"test_tool","description":"A test tool","inputSchema":{"type":"object"}}"#;
    let def: RemoteToolDef = serde_json::from_str(json).unwrap();
    assert_eq!(def.name, "test_tool");
    assert_eq!(def.description, "A test tool");
    assert_eq!(def.input_schema["type"], "object");
}

#[test]
fn remote_tool_def_minimal() {
    let json = r#"{"name":"minimal","description":"No schema"}"#;
    let def: RemoteToolDef = serde_json::from_str(json).unwrap();
    assert_eq!(def.input_schema, serde_json::Value::Null);
}

// ═══════════════════════════════════════════════════════════════
// CallToolResult + ToolContent — 2 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn call_tool_result_text_content() {
    let json = r#"{"content":[{"type":"text","text":"hello world"}]}"#;
    let result: CallToolResult = serde_json::from_str(json).unwrap();
    assert_eq!(result.content.len(), 1);
    assert_eq!(result.content[0].content_type, "text");
    assert_eq!(result.content[0].text.as_deref(), Some("hello world"));
}

#[test]
fn call_tool_result_multi_content() {
    let json = r#"{"content":[{"type":"text","text":"line1"},{"type":"text","text":"line2"}]}"#;
    let result: CallToolResult = serde_json::from_str(json).unwrap();
    assert_eq!(result.content.len(), 2);
}

// ═══════════════════════════════════════════════════════════════
// JsonRpcError — 1 test
// ═══════════════════════════════════════════════════════════════

#[test]
fn json_rpc_error_deserialize() {
    let json = r#"{"code":-32000,"message":"Method not found"}"#;
    let err: JsonRpcError = serde_json::from_str(json).unwrap();
    assert_eq!(err.code, -32000);
    assert_eq!(err.message, "Method not found");
}
