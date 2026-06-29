use crate::types::{ToolCallItem, ToolCallSet, ToolError, ToolResultItem, ToolResultSet};
use serde_json::Value;

// ═══════════════════════════════════════════════════════════════
// ToolError — 9 tests
// ═══════════════════════════════════════════════════════════════

// [构造] From<&str> 正确设置 message
#[test]
fn tool_error_from_str() {
    let e = ToolError::from("file not found");
    assert_eq!(e.message, "file not found");
}

// [构造] From<String> 正确设置 message
#[test]
fn tool_error_from_string() {
    let e = ToolError::from(String::from("permission denied"));
    assert_eq!(e.message, "permission denied");
}

// [trait] Display 输出 message 本身
#[test]
fn tool_error_display() {
    let e = ToolError::from("timeout");
    assert_eq!(format!("{e}"), "timeout");
}

// [trait] std::error::Error 满足 trait 约束
#[test]
fn tool_error_implements_std_error() {
    fn takes_error(_e: impl std::error::Error) {}
    takes_error(ToolError::from("test"));
}

// [trait] Debug 输出包含 message
#[test]
fn tool_error_debug() {
    let e = ToolError::from("crash");
    let debug = format!("{e:?}");
    assert!(debug.contains("crash"));
}

// [trait] Clone 克隆后 message 相等
#[test]
fn tool_error_clone() {
    let e = ToolError::from("original");
    assert_eq!(e.message, e.clone().message);
}

// [边界] 空字符串 message：不 panic
#[test]
fn tool_error_empty_message() {
    let e = ToolError::from("");
    assert_eq!(e.message, "");
    assert_eq!(format!("{e}"), "");
}

// [边界] 超长 message（10KB）正常存取
#[test]
fn tool_error_long_message() {
    let long = "e".repeat(10_000);
    let e = ToolError::from(long.clone());
    assert_eq!(e.message.len(), 10_000);
}

// [边界] Unicode message（中文 + emoji）正常存取
#[test]
fn tool_error_unicode_message() {
    let e = ToolError::from("错误💥");
    assert_eq!(e.message, "错误💥");
    assert_eq!(format!("{e}"), "错误💥");
}

// ═══════════════════════════════════════════════════════════════
// ToolCallItem — 8 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 所有字段正确赋值
#[test]
fn tool_call_item_all_fields() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "read_file".into(),
        params: serde_json::json!({"path": "/tmp/x"}),
        blocked_by: vec!["call_1".into()],
        blocking: vec!["call_2".into()],
    };
    assert_eq!(call.id, "call_0");
    assert_eq!(call.tool, "read_file");
    assert_eq!(call.params["path"], "/tmp/x");
    assert_eq!(call.blocked_by, vec!["call_1"]);
    assert_eq!(call.blocking, vec!["call_2"]);
}

// [边界] blocked_by 和 blocking 为空 Vec
#[test]
fn tool_call_item_empty_deps() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "search".into(),
        params: serde_json::Value::Null,
        blocked_by: vec![],
        blocking: vec![],
    };
    assert!(call.blocked_by.is_empty());
    assert!(call.blocking.is_empty());
}

// [边界] id 为空字符串：不 panic
#[test]
fn tool_call_item_empty_id() {
    let call = ToolCallItem {
        id: "".into(),
        tool: "tool".into(),
        params: serde_json::Value::Null,
        blocked_by: vec![],
        blocking: vec![],
    };
    assert_eq!(call.id, "");
}

// [序列化] serde 往返：含依赖的完整 ToolCallItem
#[test]
fn tool_call_item_serialization_roundtrip() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "read_file".into(),
        params: serde_json::json!({"path": "/tmp/x"}),
        blocked_by: vec!["call_1".into()],
        blocking: vec!["call_2".into(), "call_3".into()],
    };
    let json = serde_json::to_string(&call).unwrap();
    let back: ToolCallItem = serde_json::from_str(&json).unwrap();
    assert_eq!(call.id, back.id);
    assert_eq!(call.tool, back.tool);
    assert_eq!(call.params, back.params);
    assert_eq!(call.blocked_by, back.blocked_by);
    assert_eq!(call.blocking, back.blocking);
}

// [序列化] 省略 blocked_by/blocking 字段 → 反序列化为空 Vec
#[test]
fn tool_call_item_deserialize_missing_deps() {
    let json = r#"{"id":"call_0","tool":"x","params":null}"#;
    let call: ToolCallItem = serde_json::from_str(json).unwrap();
    assert!(call.blocked_by.is_empty());
    assert!(call.blocking.is_empty());
}

// [trait] Clone 克隆后字段一致
#[test]
fn tool_call_item_clone() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "tool".into(),
        params: serde_json::json!({"x": 1}),
        blocked_by: vec!["call_1".into()],
        blocking: vec![],
    };
    let cloned = call.clone();
    assert_eq!(call.id, cloned.id);
    assert_eq!(call.tool, cloned.tool);
    assert_eq!(call.params, cloned.params);
    assert_eq!(call.blocked_by, cloned.blocked_by);
    assert_eq!(call.blocking, cloned.blocking);
}

// [边界] params 深度嵌套（4 层）结构保留
#[test]
fn tool_call_item_deeply_nested_params() {
    let params = serde_json::json!({
        "a": {"b": {"c": {"d": [1, null, {"e": "deep"}]}}}
    });
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "deep".into(),
        params: params.clone(),
        blocked_by: vec![],
        blocking: vec![],
    };
    let json = serde_json::to_string(&call).unwrap();
    let back: ToolCallItem = serde_json::from_str(&json).unwrap();
    assert_eq!(back.params, params);
}

// [trait] Debug 输出可读
#[test]
fn tool_call_item_debug() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "read_file".into(),
        params: serde_json::json!({"path": "/x"}),
        blocked_by: vec![],
        blocking: vec![],
    };
    let debug = format!("{call:?}");
    assert!(debug.contains("call_0"));
    assert!(debug.contains("read_file"));
}

// ═══════════════════════════════════════════════════════════════
// ToolCallSet — 7 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 含多个 call 的 ToolCallSet
#[test]
fn tool_call_set_multiple_calls() {
    let set = ToolCallSet {
        session_id: "session-1".into(),
        calls: vec![
            ToolCallItem {
                id: "call_0".into(),
                tool: "read_file".into(),
                params: serde_json::json!({"path": "/a"}),
                blocked_by: vec![],
                blocking: vec!["call_1".into()],
            },
            ToolCallItem {
                id: "call_1".into(),
                tool: "write_file".into(),
                params: serde_json::json!({"path": "/b"}),
                blocked_by: vec!["call_0".into()],
                blocking: vec![],
            },
        ],
        timeout_ms: Some(5000),
    };
    assert_eq!(set.session_id, "session-1");
    assert_eq!(set.calls.len(), 2);
    assert_eq!(set.timeout_ms, Some(5000));
}

// [边界] calls 为空 Vec
#[test]
fn tool_call_set_empty_calls() {
    let set = ToolCallSet {
        session_id: "session-1".into(),
        calls: vec![],
        timeout_ms: None,
    };
    assert!(set.calls.is_empty());
    assert_eq!(set.timeout_ms, None);
}

// [边界] timeout_ms = None（Engine 不传）
#[test]
fn tool_call_set_timeout_none() {
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![],
        timeout_ms: None,
    };
    assert_eq!(set.timeout_ms, None);
}

// [序列化] serde 往返：含 timeout 的完整 ToolCallSet
#[test]
fn tool_call_set_serialization_roundtrip() {
    let set = ToolCallSet {
        session_id: "session-1".into(),
        calls: vec![ToolCallItem {
            id: "call_0".into(),
            tool: "read".into(),
            params: serde_json::json!({"path": "/x"}),
            blocked_by: vec![],
            blocking: vec![],
        }],
        timeout_ms: Some(30000),
    };
    let json = serde_json::to_string(&set).unwrap();
    let back: ToolCallSet = serde_json::from_str(&json).unwrap();
    assert_eq!(back.session_id, "session-1");
    assert_eq!(back.calls.len(), 1);
    assert_eq!(back.timeout_ms, Some(30000));
}

// [序列化] 省略 timeout_ms → 反序列化为 None
#[test]
fn tool_call_set_deserialize_missing_timeout() {
    let json = r#"{"session_id":"s","calls":[]}"#;
    let set: ToolCallSet = serde_json::from_str(json).unwrap();
    assert_eq!(set.timeout_ms, None);
}

// [trait] Clone 克隆后字段一致
#[test]
fn tool_call_set_clone() {
    let set = ToolCallSet {
        session_id: "sid".into(),
        calls: vec![ToolCallItem {
            id: "c0".into(),
            tool: "t".into(),
            params: serde_json::json!(null),
            blocked_by: vec![],
            blocking: vec![],
        }],
        timeout_ms: Some(1000),
    };
    let cloned = set.clone();
    assert_eq!(set.session_id, cloned.session_id);
    assert_eq!(set.calls.len(), cloned.calls.len());
    assert_eq!(set.timeout_ms, cloned.timeout_ms);
}

// [边界] session_id 为空字符串：不 panic
#[test]
fn tool_call_set_empty_session_id() {
    let set = ToolCallSet {
        session_id: "".into(),
        calls: vec![],
        timeout_ms: None,
    };
    assert_eq!(set.session_id, "");
}

// ═══════════════════════════════════════════════════════════════
// ToolResultItem — 10 tests
// ═══════════════════════════════════════════════════════════════

fn make_result_item(
    call_id: &str,
    name: &str,
    status: &str,
    result: Value,
    error: Option<&str>,
) -> ToolResultItem {
    ToolResultItem {
        call_id: call_id.into(),
        name: name.into(),
        status: status.into(),
        result,
        error: error.map(|s| s.into()),
    }
}

// ── 构造 & 序列化 ──

// [构造] success 状态：result 有值，error 为 None，name 正确
#[test]
fn tool_result_item_success() {
    let item = make_result_item("call_0", "read_file", "success", serde_json::json!({"ok": true, "data": [1, 2, 3]}), None);
    assert_eq!(item.status, "success");
    assert_eq!(item.name, "read_file");
    assert_eq!(item.result["ok"], true);
    assert!(item.error.is_none());
}

// [构造] error 状态：result 为 null，error 有值
#[test]
fn tool_result_item_error() {
    let item = make_result_item("call_1", "search", "error", Value::Null, Some("file not found"));
    assert_eq!(item.status, "error");
    assert_eq!(item.result, serde_json::Value::Null);
    assert_eq!(item.error, Some("file not found".into()));
}

// [构造] cancelled 状态：result 为 null，error 解释原因
#[test]
fn tool_result_item_cancelled() {
    let item = make_result_item("call_2", "write_file", "cancelled", Value::Null, Some("cancelled: dependency call_1 failed"));
    assert_eq!(item.status, "cancelled");
    assert_eq!(item.result, serde_json::Value::Null);
    assert!(item.error.unwrap().contains("cancelled"));
}

// [序列化] success 不输出 error 字段
#[test]
fn tool_result_item_success_skips_error() {
    let item = make_result_item("call_0", "read_file", "success", serde_json::json!({"ok": true}), None);
    let json = serde_json::to_string(&item).unwrap();
    assert!(!json.contains("\"error\""));
    assert!(json.contains("success"));
}

// [序列化] error 状态输出 error 字段
#[test]
fn tool_result_item_error_includes_error_field() {
    let item = make_result_item("call_1", "search", "error", Value::Null, Some("timeout"));
    let json = serde_json::to_string(&item).unwrap();
    assert!(json.contains("\"error\""));
    assert!(json.contains("timeout"));
}

// [序列化] serde 往返 success（含 name 字段）
#[test]
fn tool_result_item_serialization_roundtrip_success() {
    let item = make_result_item("call_0", "read_file", "success", serde_json::json!({"deleted": 42}), None);
    let json = serde_json::to_string(&item).unwrap();
    let back: ToolResultItem = serde_json::from_str(&json).unwrap();
    assert_eq!(back.call_id, "call_0");
    assert_eq!(back.name, "read_file");
    assert_eq!(back.status, "success");
    assert_eq!(back.result["deleted"], 42);
    assert_eq!(back.error, None);
}

// [序列化] serde 往返 error
#[test]
fn tool_result_item_serialization_roundtrip_error() {
    let item = make_result_item("call_1", "search", "error", Value::Null, Some("bad input"));
    let json = serde_json::to_string(&item).unwrap();
    let back: ToolResultItem = serde_json::from_str(&json).unwrap();
    assert_eq!(back.status, "error");
    assert_eq!(back.error, Some("bad input".into()));
}

// [trait] Clone 克隆后字段一致
#[test]
fn tool_result_item_clone() {
    let item = make_result_item("call_0", "t", "success", serde_json::json!({"x": 1}), None);
    let cloned = item.clone();
    assert_eq!(item.call_id, cloned.call_id);
    assert_eq!(item.name, cloned.name);
    assert_eq!(item.status, cloned.status);
    assert_eq!(item.result, cloned.result);
    assert_eq!(item.error, cloned.error);
}

// [边界] call_id 为空字符串：不 panic
#[test]
fn tool_result_item_empty_call_id() {
    let item = make_result_item("", "t", "success", Value::Null, None);
    assert_eq!(item.call_id, "");
}

// [边界] result 为深度嵌套 JSON：结构保留
#[test]
fn tool_result_item_deeply_nested_result() {
    let result = serde_json::json!({
        "files": [{"name": "a.rs", "matches": [{"line": 1, "col": 2}]}]
    });
    let item = make_result_item("call_0", "search", "success", result.clone(), None);
    let json = serde_json::to_string(&item).unwrap();
    let back: ToolResultItem = serde_json::from_str(&json).unwrap();
    assert_eq!(back.result, result);
}

// ═══════════════════════════════════════════════════════════════
// ToolResultSet — 6 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 含多个结果
#[test]
fn tool_result_set_multiple_results() {
    let set = ToolResultSet {
        session_id: "session-1".into(),
        results: vec![
            make_result_item("call_0", "read_file", "success", serde_json::json!("content"), None),
            make_result_item("call_1", "search", "error", Value::Null, Some("failed")),
            make_result_item("call_2", "write_file", "cancelled", Value::Null, Some("cancelled: upstream call_1 failed")),
        ],
    };
    assert_eq!(set.session_id, "session-1");
    assert_eq!(set.results.len(), 3);
    assert_eq!(set.results[0].status, "success");
    assert_eq!(set.results[1].status, "error");
    assert_eq!(set.results[2].status, "cancelled");
}

// [边界] results 为空 Vec
#[test]
fn tool_result_set_empty_results() {
    let set = ToolResultSet {
        session_id: "session-1".into(),
        results: vec![],
    };
    assert!(set.results.is_empty());
}

// [序列化] serde 往返
#[test]
fn tool_result_set_serialization_roundtrip() {
    let set = ToolResultSet {
        session_id: "session-1".into(),
        results: vec![
            make_result_item("call_0", "read_file", "success", serde_json::json!({"ok": true}), None),
            make_result_item("call_1", "search", "error", Value::Null, Some("boom")),
        ],
    };
    let json = serde_json::to_string(&set).unwrap();
    let back: ToolResultSet = serde_json::from_str(&json).unwrap();
    assert_eq!(back.session_id, "session-1");
    assert_eq!(back.results.len(), 2);
    assert_eq!(back.results[0].call_id, "call_0");
    assert_eq!(back.results[0].name, "read_file");
    assert_eq!(back.results[1].call_id, "call_1");
}

// [trait] Clone 克隆后一致
#[test]
fn tool_result_set_clone() {
    let set = ToolResultSet {
        session_id: "sid".into(),
        results: vec![make_result_item("c0", "t", "success", serde_json::json!(null), None)],
    };
    let cloned = set.clone();
    assert_eq!(set.session_id, cloned.session_id);
    assert_eq!(set.results.len(), cloned.results.len());
}

// [边界] session_id 为空字符串
#[test]
fn tool_result_set_empty_session_id() {
    let set = ToolResultSet {
        session_id: "".into(),
        results: vec![],
    };
    assert_eq!(set.session_id, "");
}

// [trait] Debug 输出可读
#[test]
fn tool_result_set_debug() {
    let set = ToolResultSet {
        session_id: "sid".into(),
        results: vec![make_result_item("c0", "t", "success", serde_json::json!({"x": 1}), None)],
    };
    let debug = format!("{set:?}");
    assert!(debug.contains("sid"));
    assert!(debug.contains("c0"));
}
