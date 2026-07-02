//! AgentConfig — top-level declarative agent configuration.

use serde::{Deserialize, Serialize};

use crate::{ModelDecl, ResourceSpec, ToolSpec};

/// Declarative agent configuration — pure data, no behavior.
///
/// AgentConfig declares WHAT an agent needs. Engine (Phase 4) reads it
/// and figures out HOW to resolve each logical resource to concrete
/// NodeIds on the Bus.
///
/// AgentConfig knows nothing about the Bus, NodeIds, or whether
/// resources are online. It only speaks logical names.
///
/// Required fields (no default, must be present in JSON):
/// - `system_prompt` — injected at the start of every model call
/// - `models` — at least one model in priority order
/// - `allowed_paths` — mandatory when `tools` is non-empty
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentConfig {
    /// System prompt injected at the start of every model call.
    pub system_prompt: String,

    /// Models in priority order. Engine picks the first one whose
    /// model node is online on the Bus. Must not be empty.
    pub models: Vec<ModelDecl>,

    /// Tools this agent may use, each with permission constraints.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tools: Vec<ToolSpec>,

    /// File system paths this agent is allowed to access.
    /// Sandbox enforces these boundaries.
    /// Required when `tools` is non-empty.
    pub allowed_paths: Vec<String>,

    /// Subagents this agent can delegate tasks to.
    /// One ResourceSpec may resolve to N NodeIds on the Bus.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub subagents: Vec<ResourceSpec>,

    /// Teammates this agent can coordinate with.
    /// One ResourceSpec may resolve to N NodeIds on the Bus.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub teammates: Vec<ResourceSpec>,
}

/// Validation errors returned by [`AgentConfig::validate`].
#[derive(Debug, Clone, PartialEq)]
pub enum ConfigError {
    /// `system_prompt` is empty.
    SystemPromptEmpty,
    /// `models` is empty — at least one model is required.
    ModelsEmpty,
    /// `tools` is non-empty but `allowed_paths` is empty.
    AllowedPathsEmpty,
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::SystemPromptEmpty => write!(f, "system_prompt must not be empty"),
            Self::ModelsEmpty => write!(f, "models must contain at least one ModelDecl"),
            Self::AllowedPathsEmpty => {
                write!(
                    f,
                    "allowed_paths must not be empty when tools are configured"
                )
            }
        }
    }
}

impl AgentConfig {
    /// Create a new AgentConfig with all optional fields empty.
    ///
    /// Required fields (`system_prompt`, `models`, `allowed_paths`) start
    /// empty — call [`validate`](Self::validate) before passing to Engine.
    pub fn new() -> Self {
        Self {
            system_prompt: String::new(),
            models: Vec::new(),
            tools: Vec::new(),
            allowed_paths: Vec::new(),
            subagents: Vec::new(),
            teammates: Vec::new(),
        }
    }

    /// Validate that the configuration satisfies all invariants.
    ///
    /// Engine calls this at the start of `init()` and rejects invalid
    /// configurations before connecting to the Bus.
    pub fn validate(&self) -> Result<(), Vec<ConfigError>> {
        let mut errors = Vec::new();

        if self.system_prompt.is_empty() {
            errors.push(ConfigError::SystemPromptEmpty);
        }
        if self.models.is_empty() {
            errors.push(ConfigError::ModelsEmpty);
        }
        if !self.tools.is_empty() && self.allowed_paths.is_empty() {
            errors.push(ConfigError::AllowedPathsEmpty);
        }

        if errors.is_empty() {
            Ok(())
        } else {
            Err(errors)
        }
    }
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ModelDecl, ResourceSpec, ToolPermission, ToolSpec};
    use serde_json;

    // ═══════════════════════════════════════════════════════════════
    // AgentConfig — 15 tests
    // ═══════════════════════════════════════════════════════════════

    // Helper: a minimal valid config that passes validate()
    fn minimal_valid_config() -> AgentConfig {
        AgentConfig {
            system_prompt: "You are helpful.".into(),
            models: vec![ModelDecl {
                provider: "deepseek".into(),
                model_name: "deepseek-flash".into(),
                thinking_enabled: false,
                temperature: None,
                max_output_tokens: None,
                endpoint: None,
                api_key_env: None,
                extra: serde_json::Value::Null,
            }],
            tools: vec![],
            allowed_paths: vec![],
            subagents: vec![],
            teammates: vec![],
        }
    }

    // [构造] new() 创建全空字段的配置
    #[test]
    fn agent_config_new_is_empty() {
        let config = AgentConfig::new();
        assert_eq!(config.system_prompt, "");
        assert!(config.models.is_empty());
        assert!(config.tools.is_empty());
        assert!(config.allowed_paths.is_empty());
        assert!(config.subagents.is_empty());
        assert!(config.teammates.is_empty());
    }

    // [trait] Default：AgentConfig::default() 等于 AgentConfig::new()
    #[test]
    fn agent_config_default_equals_new() {
        assert_eq!(AgentConfig::default(), AgentConfig::new());
    }

    // [构造] 含所有字段的完整配置正确存储
    #[test]
    fn agent_config_with_all_fields() {
        let model = ModelDecl {
            provider: "deepseek".into(),
            model_name: "deepseek-flash".into(),
            thinking_enabled: true,
            temperature: Some(0.7),
            max_output_tokens: Some(4096),
            endpoint: None,
            api_key_env: None,
            extra: serde_json::Value::Null,
        };
        let tool = ToolSpec {
            name: "read_file".into(),
            permission: ToolPermission::Allow,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        let sub = ResourceSpec {
            name: "code_reviewer".into(),
            node_type: "agent/subagent".into(),
            capabilities: None,
        };
        let config = AgentConfig {
            system_prompt: "You are helpful.".into(),
            models: vec![model.clone()],
            tools: vec![tool.clone()],
            allowed_paths: vec!["/workspace".into()],
            subagents: vec![sub.clone()],
            teammates: vec![],
        };
        assert_eq!(config.system_prompt, "You are helpful.");
        assert_eq!(config.models.len(), 1);
        assert_eq!(config.models[0], model);
        assert_eq!(config.tools.len(), 1);
        assert_eq!(config.tools[0], tool);
        assert_eq!(config.allowed_paths.len(), 1);
        assert_eq!(config.subagents.len(), 1);
        assert!(config.teammates.is_empty());
    }

    // [trait] Clone：克隆后相等
    #[test]
    fn agent_config_clone() {
        let config = minimal_valid_config();
        assert_eq!(config, config.clone());
    }

    // [trait] PartialEq：相同字段相等
    #[test]
    fn agent_config_equality() {
        let a = minimal_valid_config();
        let b = minimal_valid_config();
        let c = AgentConfig {
            system_prompt: "different".into(),
            ..minimal_valid_config()
        };
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [序列化] 最小合法配置 serde 往返
    #[test]
    fn agent_config_minimal_serialization_roundtrip() {
        let config = minimal_valid_config();
        let json = serde_json::to_string(&config).unwrap();
        let back: AgentConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(config, back);
        assert!(json.contains("system_prompt"));
        assert!(json.contains("models"));
    }

    // [序列化] 含数据的完整往返
    #[test]
    fn agent_config_full_serialization_roundtrip() {
        let config = AgentConfig {
            system_prompt: "You are an assistant.".into(),
            models: vec![ModelDecl {
                provider: "deepseek".into(),
                model_name: "deepseek-flash".into(),
                thinking_enabled: true,
                temperature: Some(0.5),
                max_output_tokens: Some(8192),
                endpoint: None,
                api_key_env: None,
                extra: serde_json::json!({"top_p": 0.95}),
            }],
            tools: vec![ToolSpec {
                name: "search".into(),
                permission: ToolPermission::Ask,
                parameter_filter: None,
                description: Some("Search the web".into()),
                parameters: None,
            }],
            allowed_paths: vec!["/workspace".into(), "/tmp".into()],
            subagents: vec![ResourceSpec {
                name: "reviewer".into(),
                node_type: "agent/subagent".into(),
                capabilities: Some(serde_json::json!({"skills": ["code_review"]})),
            }],
            teammates: vec![],
        };
        let json = serde_json::to_string(&config).unwrap();
        let back: AgentConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(config, back);
        assert_eq!(back.models.len(), 1);
        assert_eq!(back.tools.len(), 1);
        assert_eq!(back.subagents.len(), 1);
    }

    // [序列化] tools/subagents/teammates 为空时不输出到 JSON
    #[test]
    fn agent_config_optional_fields_skipped() {
        let config = minimal_valid_config();
        let json = serde_json::to_string(&config).unwrap();
        assert!(!json.contains("\"tools\""));
        assert!(!json.contains("\"subagents\""));
        assert!(!json.contains("\"teammates\""));
    }

    // [兼容] JSON 缺少 system_prompt → 反序列化报错
    #[test]
    fn agent_config_deserialize_missing_system_prompt_fails() {
        let json = r#"{"models":[{"provider":"x","model_name":"y"}],"allowed_paths":[]}"#;
        let result: Result<AgentConfig, _> = serde_json::from_str(json);
        assert!(result.is_err());
    }

    // [兼容] JSON 缺少 models → 反序列化报错
    #[test]
    fn agent_config_deserialize_missing_models_fails() {
        let json = r#"{"system_prompt":"hello","allowed_paths":[]}"#;
        let result: Result<AgentConfig, _> = serde_json::from_str(json);
        assert!(result.is_err());
    }

    // [兼容] JSON 缺少 allowed_paths → 反序列化报错
    #[test]
    fn agent_config_deserialize_missing_allowed_paths_fails() {
        let json = r#"{"system_prompt":"hello","models":[{"provider":"x","model_name":"y"}]}"#;
        let result: Result<AgentConfig, _> = serde_json::from_str(json);
        assert!(result.is_err());
    }

    // [兼容] JSON 包含 tools 但缺少 options 字段 → 默认值
    #[test]
    fn agent_config_deserialize_tools_with_minimal_fields() {
        let json = r#"{"system_prompt":"s","models":[{"provider":"x","model_name":"y"}],"allowed_paths":["/x"],"tools":[{"name":"t","permission":"Allow"}]}"#;
        let config: AgentConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.tools.len(), 1);
        assert_eq!(config.tools[0].name, "t");
        assert_eq!(config.tools[0].permission, ToolPermission::Allow);
        assert!(config.subagents.is_empty());
        assert!(config.teammates.is_empty());
    }

    // ── validate() ──────────────────────────────────────────────────

    // [校验] 合法配置通过 validate()
    #[test]
    fn validate_passes_for_valid_config() {
        assert!(minimal_valid_config().validate().is_ok());
    }

    // [校验] validate passes: 无工具时 allowed_paths 可为空
    #[test]
    fn validate_allowed_paths_empty_ok_when_no_tools() {
        let config = AgentConfig {
            tools: vec![],
            allowed_paths: vec![],
            ..minimal_valid_config()
        };
        assert!(config.validate().is_ok());
    }

    // [校验] validate fails: system_prompt 为空
    #[test]
    fn validate_fails_on_empty_system_prompt() {
        let config = AgentConfig {
            system_prompt: "".into(),
            ..minimal_valid_config()
        };
        let errors = config.validate().unwrap_err();
        assert!(errors.contains(&ConfigError::SystemPromptEmpty));
    }

    // [校验] validate fails: models 为空
    #[test]
    fn validate_fails_on_empty_models() {
        let config = AgentConfig {
            models: vec![],
            ..minimal_valid_config()
        };
        let errors = config.validate().unwrap_err();
        assert!(errors.contains(&ConfigError::ModelsEmpty));
    }

    // [校验] validate fails: 有工具但 allowed_paths 为空
    #[test]
    fn validate_fails_on_tools_without_allowed_paths() {
        let config = AgentConfig {
            tools: vec![ToolSpec {
                name: "read_file".into(),
                permission: ToolPermission::Allow,
                parameter_filter: None,
                description: None,
                parameters: None,
            }],
            allowed_paths: vec![],
            ..minimal_valid_config()
        };
        let errors = config.validate().unwrap_err();
        assert!(errors.contains(&ConfigError::AllowedPathsEmpty));
    }

    // [校验] validate 可累计多个错误
    #[test]
    fn validate_accumulates_multiple_errors() {
        let config = AgentConfig {
            system_prompt: "".into(),
            models: vec![],
            tools: vec![ToolSpec {
                name: "t".into(),
                permission: ToolPermission::Allow,
                parameter_filter: None,
                description: None,
                parameters: None,
            }],
            allowed_paths: vec![],
            ..AgentConfig::default()
        };
        let errors = config.validate().unwrap_err();
        assert!(errors.contains(&ConfigError::SystemPromptEmpty));
        assert!(errors.contains(&ConfigError::ModelsEmpty));
        assert!(errors.contains(&ConfigError::AllowedPathsEmpty));
    }
}
