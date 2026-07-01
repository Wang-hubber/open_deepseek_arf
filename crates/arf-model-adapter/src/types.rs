//! Shared types for ModelAdapter — payloads, params, tool defs, responses.

use arf_core::ModelMessage;
use serde::{Deserialize, Serialize};

/// Model inference parameters extracted from ModelSpec.
///
/// These are ARF-standard params. Each Provider translates them to
/// its native API format.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ModelParams {
    /// Sampling temperature (0.0–2.0). None = provider default.
    pub temperature: Option<f32>,
    /// Hard limit on output tokens. None = provider default.
    /// Note: Anthropic requires max_tokens — if None, provider uses a safe default.
    pub max_tokens: Option<u32>,
    /// Whether thinking/reasoning is enabled.
    pub thinking_enabled: bool,
    /// Provider-specific extra parameters (e.g., top_p, reasoning_effort).
    pub extra: serde_json::Value,
}

/// Tool definition sent to the model for function calling.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDef {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
}

/// Payload of a `model_call` Bus message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelCallPayload {
    pub messages: Vec<ModelMessage>,
    pub tools: Vec<ToolDef>,
    /// Model inference parameters. Optional on the wire so the engine can
    /// also send a `ModelCall` (core type) which lacks this field — the
    /// missing field defaults to `ModelParams::default()`.
    #[serde(default)]
    pub model_params: ModelParams,
    /// Whether to stream the response. Default true.
    #[serde(default = "default_stream")]
    pub stream: bool,
}

fn default_stream() -> bool {
    true
}

/// A single chunk in a streaming response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelResponseChunk {
    /// Chunk type: "text", "reasoning", "tool_call", "usage".
    pub chunk_type: String,
    /// Text delta (for "text" chunks).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content: Option<String>,
    /// Reasoning delta (DeepSeek thinking mode, for "reasoning" chunks).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reasoning: Option<String>,
    /// Tool call delta (for "tool_call" chunks).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call: Option<ToolCallDelta>,
    /// Final usage stats (sent on "usage" chunk).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usage: Option<Usage>,
}

/// Incremental tool call update during streaming.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallDelta {
    pub index: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// JSON fragment — caller accumulates across chunks.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub arguments_delta: Option<String>,
}

/// Payload of a `model_response` Bus message (sent after stream ends).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelResponsePayload {
    pub message: ModelMessage,
    pub tool_calls: Option<Vec<ToolCall>>,
    pub finish_reason: String,
    pub usage: Option<Usage>,
    pub id: String,
    pub model: String,
}

/// A tool call request from the model.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: serde_json::Value,
}

/// Token usage statistics.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: u32,
    pub output_tokens: u32,
    pub total_tokens: u32,
}

#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::ModelMessage;

    // ═══════════════════════════════════════════════════════════
    // ModelParams — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [构造] 所有字段显式赋值可读
    #[test]
    fn model_params_all_fields() {
        let p = ModelParams {
            temperature: Some(0.7),
            max_tokens: Some(4096),
            thinking_enabled: true,
            extra: serde_json::json!({"reasoning_effort": "high"}),
        };
        assert_eq!(p.temperature, Some(0.7));
        assert!(p.thinking_enabled);
    }

    // [序列化] None 和 false 正确往返
    #[test]
    fn model_params_serialization_roundtrip() {
        let p = ModelParams {
            temperature: None,
            max_tokens: None,
            thinking_enabled: false,
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&p).unwrap();
        let back: ModelParams = serde_json::from_str(&json).unwrap();
        assert_eq!(back.temperature, None);
        assert!(!back.thinking_enabled);
    }

    // ═══════════════════════════════════════════════════════════
    // ToolDef — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [序列化] ToolDef 含复杂 parameters JSON 往返
    #[test]
    fn tool_def_serialization_roundtrip() {
        let t = ToolDef {
            name: "read_file".into(),
            description: "Read a file".into(),
            parameters: serde_json::json!({"type": "object"}),
        };
        let json = serde_json::to_string(&t).unwrap();
        let back: ToolDef = serde_json::from_str(&json).unwrap();
        assert_eq!(back.name, "read_file");
        assert_eq!(back.description, "Read a file");
    }

    // [trait] Clone 后值相等
    #[test]
    fn tool_def_clone() {
        let t = ToolDef {
            name: "t".into(),
            description: "d".into(),
            parameters: serde_json::json!({}),
        };
        assert_eq!(t.name, t.clone().name);
    }

    // ═══════════════════════════════════════════════════════════
    // ModelCallPayload — 3 tests
    // ═══════════════════════════════════════════════════════════

    // [序列化] 含 messages + params 往返
    #[test]
    fn model_call_payload_serialization_roundtrip() {
        let payload = ModelCallPayload {
            messages: vec![ModelMessage::new("user", "hello")],
            tools: vec![],
            model_params: ModelParams {
                temperature: Some(0.5),
                max_tokens: None,
                thinking_enabled: false,
                extra: serde_json::Value::Null,
            },
            stream: false,
        };
        let json = serde_json::to_string(&payload).unwrap();
        let back: ModelCallPayload = serde_json::from_str(&json).unwrap();
        assert_eq!(back.messages.len(), 1);
        assert_eq!(back.model_params.temperature, Some(0.5));
        assert!(!back.stream);
    }

    // [构造] tools 非空时正确存储
    #[test]
    fn model_call_payload_with_tools() {
        let payload = ModelCallPayload {
            messages: vec![],
            tools: vec![ToolDef {
                name: "search".into(),
                description: "Search the web".into(),
                parameters: serde_json::json!({"type": "object"}),
            }],
            model_params: ModelParams {
                temperature: None,
                max_tokens: None,
                thinking_enabled: false,
                extra: serde_json::Value::Null,
            },
            stream: true,
        };
        assert_eq!(payload.tools.len(), 1);
        assert!(payload.stream);
    }

    // [边界] stream 字段默认 true（缺字段反序列化）
    #[test]
    fn model_call_payload_stream_defaults_true() {
        let json = r#"{"messages":[],"tools":[],"model_params":{"thinking_enabled":false,"extra":null}}"#;
        let payload: ModelCallPayload = serde_json::from_str(json).unwrap();
        assert!(payload.stream);
    }

    // ═══════════════════════════════════════════════════════════
    // ModelResponseChunk — 3 tests
    // ═══════════════════════════════════════════════════════════

    // [构造] text 类型 chunk
    #[test]
    fn chunk_text() {
        let c = ModelResponseChunk {
            chunk_type: "text".into(),
            content: Some("Hello".into()),
            reasoning: None,
            tool_call: None,
            usage: None,
        };
        assert_eq!(c.chunk_type, "text");
        assert_eq!(c.content.unwrap(), "Hello");
    }

    // [构造] tool_call 类型 chunk
    #[test]
    fn chunk_tool_call() {
        let c = ModelResponseChunk {
            chunk_type: "tool_call".into(),
            content: None,
            reasoning: None,
            tool_call: Some(ToolCallDelta {
                index: 0,
                id: Some("call_1".into()),
                name: Some("search".into()),
                arguments_delta: Some(r#"{"query":"rust"}"#.into()),
            }),
            usage: None,
        };
        assert_eq!(c.chunk_type, "tool_call");
        let tc = c.tool_call.unwrap();
        assert_eq!(tc.index, 0);
        assert_eq!(tc.name.unwrap(), "search");
    }

    // [序列化] text chunk 不输出 None 字段
    #[test]
    fn chunk_text_serialization_skips_none() {
        let c = ModelResponseChunk {
            chunk_type: "text".into(),
            content: Some("hi".into()),
            reasoning: None,
            tool_call: None,
            usage: None,
        };
        let json = serde_json::to_string(&c).unwrap();
        assert!(!json.contains("reasoning"));
        assert!(!json.contains("tool_call"));
        assert!(!json.contains("usage"));
    }

    // ═══════════════════════════════════════════════════════════
    // ModelResponsePayload — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [构造] 纯文本回复：无 tool_calls，有 usage
    #[test]
    fn model_response_payload_text_only() {
        let payload = ModelResponsePayload {
            message: ModelMessage::new("assistant", "Hello!"),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage {
                input_tokens: 10,
                output_tokens: 5,
                total_tokens: 15,
            }),
            id: "chatcmpl-123".into(),
            model: "deepseek-v4-flash".into(),
        };
        assert_eq!(payload.message.content, "Hello!");
        assert_eq!(payload.finish_reason, "stop");
        assert!(payload.tool_calls.is_none());
    }

    // [构造] 工具调用回复：有 tool_calls，finish_reason="tool_calls"
    #[test]
    fn model_response_payload_with_tool_calls() {
        let payload = ModelResponsePayload {
            message: ModelMessage::new("assistant", ""),
            tool_calls: Some(vec![ToolCall {
                id: "call_1".into(),
                name: "read_file".into(),
                arguments: serde_json::json!({"path": "/x"}),
            }]),
            finish_reason: "tool_calls".into(),
            usage: None,
            id: "chatcmpl-456".into(),
            model: "gpt-4o".into(),
        };
        let tc = payload.tool_calls.unwrap();
        assert_eq!(tc.len(), 1);
        assert_eq!(tc[0].name, "read_file");
        assert_eq!(tc[0].arguments["path"], "/x");
    }

    // ═══════════════════════════════════════════════════════════
    // ToolCall — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [序列化] ToolCall 含嵌套 arguments JSON 往返
    #[test]
    fn tool_call_serialization_roundtrip() {
        let tc = ToolCall {
            id: "call_abc".into(),
            name: "search".into(),
            arguments: serde_json::json!({"query": "rust"}),
        };
        let json = serde_json::to_string(&tc).unwrap();
        let back: ToolCall = serde_json::from_str(&json).unwrap();
        assert_eq!(back.id, "call_abc");
        assert_eq!(back.name, "search");
    }

    // [trait] Clone 后值相等
    #[test]
    fn tool_call_clone() {
        let tc = ToolCall {
            id: "x".into(),
            name: "y".into(),
            arguments: serde_json::json!({}),
        };
        assert_eq!(tc.id, tc.clone().id);
    }

    // ═══════════════════════════════════════════════════════════
    // Usage — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [序列化] Usage 往返
    #[test]
    fn usage_serialization_roundtrip() {
        let u = Usage {
            input_tokens: 100,
            output_tokens: 50,
            total_tokens: 150,
        };
        let json = serde_json::to_string(&u).unwrap();
        let back: Usage = serde_json::from_str(&json).unwrap();
        assert_eq!(back.total_tokens, 150);
    }

    // [边界] 零 token 合法（流式中间 chunk 或错误响应）
    #[test]
    fn usage_zero_tokens() {
        let u = Usage {
            input_tokens: 0,
            output_tokens: 0,
            total_tokens: 0,
        };
        assert_eq!(u.total_tokens, 0);
    }
}
