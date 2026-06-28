//! Shared types for ModelAdapter — payloads, params, tool defs, responses.

use arf_core::ModelMessage;
use serde::{Deserialize, Serialize};

/// Model inference parameters extracted from ModelSpec.
///
/// These are ARF-standard params. Each Provider translates them to
/// its native API format.
#[derive(Debug, Clone, Serialize, Deserialize)]
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
    pub model_params: ModelParams,
}

/// Payload of a `model_response` Bus message.
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
    // ModelCallPayload — 2 tests
    // ═══════════════════════════════════════════════════════════

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
        };
        let json = serde_json::to_string(&payload).unwrap();
        let back: ModelCallPayload = serde_json::from_str(&json).unwrap();
        assert_eq!(back.messages.len(), 1);
        assert_eq!(back.model_params.temperature, Some(0.5));
    }

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
        };
        assert_eq!(payload.tools.len(), 1);
        assert_eq!(payload.tools[0].name, "search");
    }

    // ═══════════════════════════════════════════════════════════
    // ModelResponsePayload — 2 tests
    // ═══════════════════════════════════════════════════════════

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
