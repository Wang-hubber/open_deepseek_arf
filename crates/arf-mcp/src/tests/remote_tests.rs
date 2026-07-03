use crate::remote::{call_result_to_output, CallToolResult, JsonRpcError, RemoteToolDef, ToolContent};

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
// F-011 — isError propagation
// ═══════════════════════════════════════════════════════════════

// [边界] isError 缺省 → false（向后兼容）
#[test]
fn call_tool_result_is_error_defaults_false() {
    let json = r#"{"content":[{"type":"text","text":"ok"}]}"#;
    let result: CallToolResult = serde_json::from_str(json).unwrap();
    assert!(!result.is_error);
}

// [方法] isError=true 反序列化 + call_result_to_output 返回 Err（F-011）
#[test]
fn http_proxy_tool_propagates_is_error() {
    let json = r#"{"content":[{"type":"text","text":"boom"}],"isError":true}"#;
    let result: CallToolResult = serde_json::from_str(json).unwrap();
    assert!(result.is_error);

    let out = call_result_to_output(result, "explode");
    let err = out.expect_err("isError:true must become ToolError");
    let msg = format!("{err}");
    assert!(msg.contains("isError"), "err should mention isError: {msg}");
    assert!(msg.contains("boom"), "err should carry the error text: {msg}");
}

// [方法] isError=false → Ok(text)
#[test]
fn call_result_to_output_success_returns_text() {
    let json = r#"{"content":[{"type":"text","text":"result-text"}]}"#;
    let result: CallToolResult = serde_json::from_str(json).unwrap();
    let out = call_result_to_output(result, "ok_tool").expect("success");
    assert_eq!(out, serde_json::Value::String("result-text".into()));
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
