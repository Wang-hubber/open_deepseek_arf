//! Shared conversion helpers — SSE parsing, retry logic.
//!
//! Used by OpenAI, DeepSeek, and any other OpenAI-compatible providers.

use serde_json::Value;

use arf_core::ModelMessage;

use crate::error::ProviderError;
use crate::types::{
    ModelResponseChunk, ModelResponsePayload, ToolCall, ToolCallDelta, Usage,
};

/// Parse SSE event stream into chunks + final response.
///
/// OpenAI-compatible SSE format: lines prefixed with `data: `,
/// terminated by `data: [DONE]`. Each data line is a JSON chunk
/// with the same shape as a non-streaming response delta.
pub(crate) fn parse_sse(
    raw: &str,
) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
    let mut chunks = Vec::new();
    let mut full_content = String::new();
    let mut full_reasoning = String::new();
    let mut acc_tool_calls: Vec<ToolCall> = Vec::new();
    let mut finish_reason = String::new();
    let mut model = String::new();
    let mut response_id = String::new();
    let mut usage: Option<Usage> = None;

    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with(':') {
            continue;
        }
        if let Some(data) = line.strip_prefix("data: ") {
            if data == "[DONE]" {
                break;
            }
            if let Ok(chunk) = serde_json::from_str::<Value>(data) {
                if let Some(m) = chunk.get("model").and_then(|v| v.as_str()) {
                    model = m.to_string();
                }
                if let Some(id) = chunk.get("id").and_then(|v| v.as_str()) {
                    response_id = id.to_string();
                }

                if let Some(choices) = chunk.get("choices").and_then(|v| v.as_array()) {
                    for choice in choices {
                        if let Some(fr) = choice.get("finish_reason").and_then(|v| v.as_str())
                            && !fr.is_empty() {
                                finish_reason = fr.to_string();
                            }

                        if let Some(delta) = choice.get("delta") {
                            // Text content
                            if let Some(c) = delta.get("content").and_then(|v| v.as_str())
                                && !c.is_empty() {
                                    full_content.push_str(c);
                                    chunks.push(ModelResponseChunk {
                                        chunk_type: "text".into(),
                                        content: Some(c.to_string()),
                                        reasoning: None,
                                        tool_call: None,
                                        usage: None,
                                    });
                                }

                            // Reasoning content (DeepSeek-specific, ignored by OpenAI)
                            if let Some(rc) =
                                delta.get("reasoning_content").and_then(|v| v.as_str())
                                && !rc.is_empty() {
                                    full_reasoning.push_str(rc);
                                    chunks.push(ModelResponseChunk {
                                        chunk_type: "reasoning".into(),
                                        content: None,
                                        reasoning: Some(rc.to_string()),
                                        tool_call: None,
                                        usage: None,
                                    });
                                }

                            // Tool calls
                            if let Some(tc_list) =
                                delta.get("tool_calls").and_then(|v| v.as_array())
                            {
                                for tc in tc_list {
                                    let index =
                                        tc.get("index").and_then(|v| v.as_u64()).unwrap_or(0)
                                            as u32;
                                    let tc_id =
                                        tc.get("id").and_then(|v| v.as_str()).map(|s| s.to_string());
                                    let tc_name = tc
                                        .get("function")
                                        .and_then(|f| f.get("name"))
                                        .and_then(|v| v.as_str())
                                        .map(|s| s.to_string());
                                    let args_delta = tc
                                        .get("function")
                                        .and_then(|f| f.get("arguments"))
                                        .and_then(|v| v.as_str())
                                        .map(|s| s.to_string());

                                    chunks.push(ModelResponseChunk {
                                        chunk_type: "tool_call".into(),
                                        content: None,
                                        reasoning: None,
                                        tool_call: Some(ToolCallDelta {
                                            index,
                                            id: tc_id.clone(),
                                            name: tc_name.clone(),
                                            arguments_delta: args_delta.clone(),
                                        }),
                                        usage: None,
                                    });

                                    if let Some(ref id) = tc_id
                                        && let Some(ref name) = tc_name
                                    {
                                        if let Some(existing) =
                                            acc_tool_calls.iter_mut().find(|tc| tc.id == *id)
                                        {
                                            if let Some(ref delta) = args_delta
                                                && let Value::String(ref mut s) = existing.arguments
                                            {
                                                s.push_str(delta);
                                            }
                                        } else {
                                            let args_str = args_delta.unwrap_or_default();
                                            let args: Value = serde_json::from_str(&args_str)
                                                .unwrap_or(Value::String(args_str));
                                            acc_tool_calls.push(ToolCall {
                                                id: id.clone(),
                                                name: name.clone(),
                                                arguments: args,
                                            });
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Usage (typically in final chunk)
                if let Some(u) = chunk.get("usage") {
                    usage = Some(Usage {
                        input_tokens: u["prompt_tokens"].as_u64().unwrap_or(0) as u32,
                        output_tokens: u["completion_tokens"].as_u64().unwrap_or(0) as u32,
                        total_tokens: u["total_tokens"].as_u64().unwrap_or(0) as u32,
                    });
                    chunks.push(ModelResponseChunk {
                        chunk_type: "usage".into(),
                        content: None,
                        reasoning: None,
                        tool_call: None,
                        usage: usage.clone(),
                    });
                }
            }
        }
    }

    let mut extra = Value::Null;
    if !full_reasoning.is_empty() {
        extra = serde_json::json!({"reasoning_content": full_reasoning});
    }

    let message = ModelMessage::new("assistant", full_content).with_extra(extra);

    let tool_calls = if acc_tool_calls.is_empty() {
        None
    } else {
        Some(acc_tool_calls)
    };

    let payload = ModelResponsePayload {
        message,
        tool_calls,
        finish_reason: if finish_reason.is_empty() {
            "stop".into()
        } else {
            finish_reason
        },
        usage,
        id: response_id,
        model,
    };

    Ok((chunks, payload))
}

/// Convert a `ToolResultItem` (from MCP) into a `ModelMessage` for the LLM conversation.
///
/// This is the integration point between MCP and ModelAdapter:
///   - `status = "success"`   → `content = result.to_string()`
///   - `status = "error"`     → `content = {"error": message}`
///   - `status = "cancelled"` → `content = {"error": "cancelled: ..."}`
///
/// All variants produce `role = "tool"`, `tool_call_id = call_id`,
/// `name = item.name`. ModelAdapter passes this directly to the model API.
pub fn tool_result_to_model_message(item: &arf_mcp::types::ToolResultItem) -> ModelMessage {
    let content = match item.status.as_str() {
        "success" => item.result.to_string(),
        "error" => serde_json::json!({
            "error": item.error.as_deref().unwrap_or("unknown error")
        })
        .to_string(),
        "cancelled" => serde_json::json!({
            "error": item.error.as_deref().unwrap_or("cancelled")
        })
        .to_string(),
        other => serde_json::json!({"error": format!("unknown status: {other}")}).to_string(),
    };
    ModelMessage::new("tool", content)
        .with_tool_call_id(&item.call_id)
        .with_name(&item.name)
}

/// Determine if an error is retryable.
pub(crate) fn is_retryable(err: &ProviderError) -> bool {
    match err {
        ProviderError::Api { status, .. } => *status == 429 || (500..600).contains(status),
        ProviderError::Transport(_) => true,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ProviderError;

    // ═══════════════════════════════════════════════════════════
    // is_retryable — 1 test
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn is_retryable_429_and_5xx() {
        assert!(is_retryable(&ProviderError::Api {
            status: 429,
            message: "rate limit".into()
        }));
        assert!(is_retryable(&ProviderError::Api {
            status: 503,
            message: "".into()
        }));
        assert!(is_retryable(&ProviderError::Transport("timeout".into())));
        assert!(!is_retryable(&ProviderError::Api {
            status: 401,
            message: "".into()
        }));
        assert!(!is_retryable(&ProviderError::Parse("".into())));
    }

    // ═══════════════════════════════════════════════════════════
    // parse_sse — same 2 tests from deepseek
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn parse_sse_text_stream() {
        let raw = concat!(
            "data: {\"id\":\"1\",\"model\":\"m\",\"choices\":[{\"delta\":{\"content\":\"Hello\"},\"index\":0}]}\n",
            "data: {\"id\":\"1\",\"model\":\"m\",\"choices\":[{\"delta\":{\"content\":\" world\"},\"index\":0}]}\n",
            "data: {\"id\":\"1\",\"model\":\"m\",\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\",\"index\":0}],\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":2,\"total_tokens\":7}}\n",
            "data: [DONE]"
        );
        let (chunks, payload) = parse_sse(raw).unwrap();
        assert_eq!(chunks.len(), 3);
        assert_eq!(payload.message.content, "Hello world");
        assert_eq!(payload.finish_reason, "stop");
        assert_eq!(payload.usage.unwrap().total_tokens, 7);
    }

    #[test]
    fn parse_sse_reasoning_stream() {
        let raw = concat!(
            "data: {\"id\":\"1\",\"model\":\"deepseek-v4-pro\",\"choices\":[{\"delta\":{\"reasoning_content\":\"thinking...\"},\"index\":0}]}\n",
            "data: {\"id\":\"1\",\"model\":\"deepseek-v4-pro\",\"choices\":[{\"delta\":{\"content\":\"answer\"},\"index\":0}]}\n",
            "data: {\"id\":\"1\",\"model\":\"deepseek-v4-pro\",\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\",\"index\":0}]}\n",
            "data: [DONE]"
        );
        let (_chunks, payload) = parse_sse(raw).unwrap();
        assert_eq!(payload.message.content, "answer");
        assert_eq!(
            payload.message.extra["reasoning_content"],
            "thinking..."
        );
    }

    // ═══════════════════════════════════════════════════════════
    // tool_result_to_model_message — 9 tests
    // ═══════════════════════════════════════════════════════════

    use arf_mcp::types::ToolResultItem;

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

    // [转换] success → ModelMessage { role="tool", tool_call_id, name, content=result_str }
    #[test]
    fn convert_success() {
        let item = make_result_item("call_0", "read_file", "success", serde_json::json!({"ok": true, "bytes": 42}), None);
        let mm = tool_result_to_model_message(&item);
        assert_eq!(mm.role, "tool");
        assert_eq!(mm.tool_call_id, Some("call_0".into()));
        assert_eq!(mm.name, Some("read_file".into()));
        let content: Value = serde_json::from_str(&mm.content).unwrap();
        assert_eq!(content["ok"], true);
        assert_eq!(content["bytes"], 42);
    }

    // [转换] success 带字符串 result → content 即 JSON 字符串
    #[test]
    fn convert_success_string_result() {
        let item = make_result_item("call_1", "read_file", "success", Value::String("file contents\nline 2".into()), None);
        let mm = tool_result_to_model_message(&item);
        assert_eq!(mm.role, "tool");
        assert_eq!(mm.content, "\"file contents\\nline 2\"");
    }

    // [转换] error → content 含 error 包装
    #[test]
    fn convert_error() {
        let item = make_result_item("call_1", "search", "error", Value::Null, Some("file not found"));
        let mm = tool_result_to_model_message(&item);
        assert_eq!(mm.role, "tool");
        assert_eq!(mm.tool_call_id, Some("call_1".into()));
        assert_eq!(mm.name, Some("search".into()));
        let content: Value = serde_json::from_str(&mm.content).unwrap();
        assert_eq!(content["error"], "file not found");
    }

    // [转换] cancelled → content 含 cancelled 原因
    #[test]
    fn convert_cancelled() {
        let item = make_result_item("call_2", "write_file", "cancelled", Value::Null, Some("cancelled: dependency call_1 failed"));
        let mm = tool_result_to_model_message(&item);
        assert_eq!(mm.role, "tool");
        let content: Value = serde_json::from_str(&mm.content).unwrap();
        assert!(content["error"].as_str().unwrap().contains("cancelled"));
    }

    // [转换] error=None → content 含 "unknown error"
    #[test]
    fn convert_error_without_message() {
        let item = ToolResultItem {
            call_id: "call_3".into(),
            name: "t".into(),
            status: "error".into(),
            result: Value::Null,
            error: None,
        };
        let mm = tool_result_to_model_message(&item);
        let content: Value = serde_json::from_str(&mm.content).unwrap();
        assert_eq!(content["error"], "unknown error");
    }

    // [转换] cancelled error=None → content 含 "cancelled"
    #[test]
    fn convert_cancelled_without_message() {
        let item = ToolResultItem {
            call_id: "call_4".into(),
            name: "t".into(),
            status: "cancelled".into(),
            result: Value::Null,
            error: None,
        };
        let mm = tool_result_to_model_message(&item);
        let content: Value = serde_json::from_str(&mm.content).unwrap();
        assert_eq!(content["error"], "cancelled");
    }

    // [边界] 超大 result（100KB）→ content 完整保留
    #[test]
    fn convert_large_result() {
        let large_str = "x".repeat(100_000);
        let item = make_result_item("call_0", "read_file", "success", Value::String(large_str.clone()), None);
        let mm = tool_result_to_model_message(&item);
        let expected = Value::String(large_str).to_string();
        assert_eq!(mm.content, expected);
    }

    // [边界] name 为空 → ModelMessage.name = Some("")
    #[test]
    fn convert_empty_name() {
        let item = make_result_item("call_0", "", "success", serde_json::json!({"x": 1}), None);
        let mm = tool_result_to_model_message(&item);
        assert_eq!(mm.name, Some("".into()));
    }

    // [边界] call_id 为空 → ModelMessage.tool_call_id = Some("")
    #[test]
    fn convert_empty_call_id() {
        let item = make_result_item("", "t", "success", serde_json::json!({"x": 1}), None);
        let mm = tool_result_to_model_message(&item);
        assert_eq!(mm.tool_call_id, Some("".into()));
    }
}
