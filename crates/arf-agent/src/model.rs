//! Agent model declaration — provider, model, endpoint, inference parameters.

use serde::{Deserialize, Serialize};

/// A single model declaration.
///
/// `ModelDecl` is a pure data declaration. It uses logical names
/// (`provider` + `model_name`) and does not reference any Bus NodeId.
/// Engine resolves it to a concrete `node_type="model"` node at build time.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ModelDecl {
    /// Provider identifier: `"deepseek"`, `"openai"`, `"anthropic"`, `"minimax"`.
    pub provider: String,

    /// Model name: `"deepseek-v4-flash"`, `"gpt-4o"`, `"claude-sonnet-4-6"`.
    pub model_name: String,

    /// Override the provider's default API endpoint.
    /// `None` means use the provider's built-in default.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub endpoint: Option<String>,

    /// Environment variable name for the API key (e.g. `"DEEPSEEK_API_KEY"`).
    /// `None` means use the provider's default env var.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_key_env: Option<String>,

    /// Whether thinking/reasoning is enabled for this model.
    /// Provider default if model doesn't support thinking.
    #[serde(default)]
    pub thinking_enabled: bool,

    /// Sampling temperature (0.0–2.0). Provider default if unset.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f64>,

    /// Hard limit on output tokens. Provider default if unset.
    #[serde(default, skip_serializing_if = "Option::is_none")]
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
    // ModelDecl — 13 tests (11 original + 2 new endpoint/api_key_env)
    // ═══════════════════════════════════════════════════════════════

    fn model_decl(provider: &str, model_name: &str) -> ModelDecl {
        ModelDecl {
            provider: provider.into(),
            model_name: model_name.into(),
            ..Default::default()
        }
    }

    // [构造] 所有字段显式赋值可读，值正确
    #[test]
    fn model_decl_all_fields() {
        let spec = ModelDecl {
            provider: "deepseek".into(),
            model_name: "deepseek-v4-flash".into(),
            endpoint: Some("https://api.deepseek.com/v1".into()),
            api_key_env: Some("DEEPSEEK_API_KEY".into()),
            thinking_enabled: true,
            temperature: Some(0.7),
            max_output_tokens: Some(4096),
            extra: serde_json::json!({"top_p": 0.9}),
        };
        assert_eq!(spec.provider, "deepseek");
        assert_eq!(spec.model_name, "deepseek-v4-flash");
        assert_eq!(spec.endpoint.as_deref(), Some("https://api.deepseek.com/v1"));
        assert_eq!(spec.api_key_env.as_deref(), Some("DEEPSEEK_API_KEY"));
        assert!(spec.thinking_enabled);
        assert_eq!(spec.temperature, Some(0.7));
        assert_eq!(spec.max_output_tokens, Some(4096));
        assert_eq!(spec.extra["top_p"], 0.9);
    }

    // [边界] thinking_enabled 默认 false（旧配置缺字段兼容）
    #[test]
    fn model_decl_thinking_disabled_by_default() {
        let json = r#"{"provider":"openai","model_name":"gpt-4"}"#;
        let spec: ModelDecl = serde_json::from_str(json).unwrap();
        assert!(!spec.thinking_enabled);
        assert!(spec.endpoint.is_none());
        assert!(spec.api_key_env.is_none());
    }

    // [边界] temperature 为 None 时不序列化到 JSON
    #[test]
    fn model_decl_temperature_none_skipped() {
        let spec = model_decl("openai", "gpt-4o");
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("temperature"));
    }

    // [边界] max_output_tokens 为 None 时不序列化到 JSON
    #[test]
    fn model_decl_max_tokens_none_skipped() {
        let spec = model_decl("deepseek", "deepseek-v4-flash");
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("max_output_tokens"));
    }

    // [边界] extra 为 Null 时不序列化到 JSON
    #[test]
    fn model_decl_extra_null_skipped() {
        let spec = model_decl("deepseek", "deepseek-v4-flash");
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("extra"));
    }

    // [边界] endpoint / api_key_env 为 None 时不序列化到 JSON
    #[test]
    fn model_decl_endpoint_api_key_none_skipped() {
        let spec = model_decl("deepseek", "deepseek-v4-flash");
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("endpoint"));
        assert!(!json.contains("api_key_env"));
    }

    // [边界] 最小合法 JSON：仅 provider + model_name，其余取默认值
    #[test]
    fn model_decl_minimal_json() {
        let json = r#"{"provider":"anthropic","model_name":"claude-sonnet-4-6"}"#;
        let spec: ModelDecl = serde_json::from_str(json).unwrap();
        assert_eq!(spec.provider, "anthropic");
        assert_eq!(spec.model_name, "claude-sonnet-4-6");
        assert!(!spec.thinking_enabled);
        assert_eq!(spec.temperature, None);
        assert_eq!(spec.max_output_tokens, None);
        assert_eq!(spec.extra, serde_json::Value::Null);
        assert!(spec.endpoint.is_none());
        assert!(spec.api_key_env.is_none());
    }

    // [trait] Clone：克隆后与原值相等
    #[test]
    fn model_decl_clone() {
        let spec = ModelDecl {
            provider: "deepseek".into(),
            model_name: "deepseek-v4-flash".into(),
            thinking_enabled: true,
            temperature: Some(0.5),
            ..Default::default()
        };
        assert_eq!(spec, spec.clone());
    }

    // [trait] PartialEq：相同字段相等，不同 provider 不等
    #[test]
    fn model_decl_equality() {
        let a = model_decl("x", "m");
        let b = model_decl("x", "m");
        let c = ModelDecl {
            provider: "y".into(),
            ..a.clone()
        };
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [序列化] 全字段 serde 往返：所有字段逐项一致
    #[test]
    fn model_decl_serialization_roundtrip_full() {
        let spec = ModelDecl {
            provider: "deepseek".into(),
            model_name: "deepseek-v4-flash".into(),
            thinking_enabled: true,
            temperature: Some(0.7),
            max_output_tokens: Some(8192),
            endpoint: Some("https://custom.api/v1".into()),
            api_key_env: Some("MY_KEY".into()),
            extra: serde_json::json!({"reasoning_effort": "high"}),
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ModelDecl = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [序列化] 最简 spec 往返：仅 provider + model_name
    #[test]
    fn model_decl_serialization_roundtrip_minimal() {
        let spec = model_decl("openai", "gpt-4o");
        let json = serde_json::to_string(&spec).unwrap();
        let back: ModelDecl = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [兼容] 未知字段反序列化不报错（前向兼容）
    #[test]
    fn model_decl_unknown_fields_ignored() {
        let json = r#"{"provider":"x","model_name":"y","future_field":123}"#;
        let spec: ModelDecl = serde_json::from_str(json).unwrap();
        assert_eq!(spec.provider, "x");
        assert_eq!(spec.model_name, "y");
    }
}
