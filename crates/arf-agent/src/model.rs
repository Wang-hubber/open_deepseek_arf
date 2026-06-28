//! Agent model declaration — provider, model name, inference parameters.

use serde::{Deserialize, Serialize};

/// A model that this agent may use, in priority order.
///
/// `ModelSpec` is a pure data declaration. It uses logical names
/// (`provider` + `model_name`) and does not reference any Bus NodeId.
/// Engine (Phase 4) resolves each spec to a concrete model node at runtime.
///
/// Agent declares multiple `ModelSpec`s in priority order. Engine picks
/// the first one whose provider + model_name matches an online model node.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelSpec {
    /// Provider identifier: `"deepseek"`, `"openai"`, `"anthropic"`.
    pub provider: String,

    /// Model name: `"deepseek-flash"`, `"gpt-4o"`, `"claude-sonnet-4-6"`.
    pub model_name: String,

    /// Whether thinking/reasoning is enabled for this model.
    /// Provider default if model doesn't support thinking.
    #[serde(default)]
    pub thinking_enabled: bool,

    /// Sampling temperature (0.0–2.0). Provider default if unset.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,

    /// Hard limit on output tokens. Provider default if unset.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<u32>,

    /// Provider-specific extra parameters (e.g., `top_p`, `frequency_penalty`).
    /// Passed through to the model API as-is. ModelAdapter reads this.
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json;

    // ═══════════════════════════════════════════════════════════════
    // ModelSpec — 11 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] 所有字段显式赋值可读，值正确
    #[test]
    fn model_spec_all_fields() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-flash".into(),
            thinking_enabled: true,
            temperature: Some(0.7),
            max_output_tokens: Some(4096),
            extra: serde_json::json!({"top_p": 0.9}),
        };
        assert_eq!(spec.provider, "deepseek");
        assert_eq!(spec.model_name, "deepseek-flash");
        assert!(spec.thinking_enabled);
        assert_eq!(spec.temperature, Some(0.7));
        assert_eq!(spec.max_output_tokens, Some(4096));
        assert_eq!(spec.extra["top_p"], 0.9);
    }

    // [边界] thinking_enabled 默认 false（旧配置缺字段兼容）
    #[test]
    fn model_spec_thinking_disabled_by_default() {
        let json = r#"{"provider":"openai","model_name":"gpt-4"}"#;
        let spec: ModelSpec = serde_json::from_str(json).unwrap();
        assert!(!spec.thinking_enabled);
    }

    // [边界] temperature 为 None 时不序列化到 JSON
    #[test]
    fn model_spec_temperature_none_skipped() {
        let spec = ModelSpec {
            provider: "openai".into(),
            model_name: "gpt-4o".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: Some(1024),
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("temperature"));
    }

    // [边界] max_output_tokens 为 None 时不序列化到 JSON
    #[test]
    fn model_spec_max_tokens_none_skipped() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-flash".into(),
            thinking_enabled: false,
            temperature: Some(0.3),
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("max_output_tokens"));
    }

    // [边界] extra 为 Null 时不序列化到 JSON
    #[test]
    fn model_spec_extra_null_skipped() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-flash".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("extra"));
    }

    // [边界] 最小合法 JSON：仅 provider + model_name，其余取默认值
    #[test]
    fn model_spec_minimal_json() {
        let json = r#"{"provider":"anthropic","model_name":"claude-sonnet-4-6"}"#;
        let spec: ModelSpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.provider, "anthropic");
        assert_eq!(spec.model_name, "claude-sonnet-4-6");
        assert!(!spec.thinking_enabled);
        assert_eq!(spec.temperature, None);
        assert_eq!(spec.max_output_tokens, None);
        assert_eq!(spec.extra, serde_json::Value::Null);
    }

    // [trait] Clone：克隆后与原值相等
    #[test]
    fn model_spec_clone() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-flash".into(),
            thinking_enabled: true,
            temperature: Some(0.5),
            max_output_tokens: Some(2048),
            extra: serde_json::json!({"key": "value"}),
        };
        assert_eq!(spec, spec.clone());
    }

    // [trait] PartialEq：相同字段相等，不同 provider 不等
    #[test]
    fn model_spec_equality() {
        let a = ModelSpec {
            provider: "x".into(),
            model_name: "m".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let b = ModelSpec {
            provider: "x".into(),
            model_name: "m".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let c = ModelSpec {
            provider: "y".into(),
            ..a.clone()
        };
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [序列化] 全字段 serde 往返：所有字段逐项一致
    #[test]
    fn model_spec_serialization_roundtrip_full() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-flash".into(),
            thinking_enabled: true,
            temperature: Some(0.7),
            max_output_tokens: Some(8192),
            extra: serde_json::json!({"reasoning_effort": "high"}),
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ModelSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [序列化] 最简 spec 往返：仅 provider + model_name
    #[test]
    fn model_spec_serialization_roundtrip_minimal() {
        let spec = ModelSpec {
            provider: "openai".into(),
            model_name: "gpt-4o".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ModelSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [兼容] 未知字段反序列化不报错（前向兼容）
    #[test]
    fn model_spec_unknown_fields_ignored() {
        let json = r#"{"provider":"x","model_name":"y","future_field":123}"#;
        let spec: ModelSpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.provider, "x");
        assert_eq!(spec.model_name, "y");
    }
}
