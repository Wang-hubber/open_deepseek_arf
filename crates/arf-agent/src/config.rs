//! AgentConfig — top-level declarative agent configuration.

use serde::{Deserialize, Serialize};

use crate::{ModelSpec, ResourceSpec, ToolSpec};

/// Declarative agent configuration — pure data, no behavior.
///
/// AgentConfig declares WHAT an agent needs. Engine (Phase 4) reads it
/// and figures out HOW to resolve each logical resource to concrete
/// NodeIds on the Bus.
///
/// AgentConfig knows nothing about the Bus, NodeIds, or whether
/// resources are online. It only speaks logical names.
///
/// All fields use `#[serde(default)]`: an empty AgentConfig is valid.
/// Missing fields in YAML/JSON deserialization do not error.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentConfig {
    /// System prompt injected at the start of every model call.
    #[serde(default)]
    pub system_prompt: String,

    /// Models in priority order. Engine picks the first one whose
    /// model node is online on the Bus.
    #[serde(default)]
    pub models: Vec<ModelSpec>,

    /// Tools this agent may use, each with permission constraints.
    #[serde(default)]
    pub tools: Vec<ToolSpec>,

    /// File system paths this agent is allowed to access.
    /// Sandbox enforces these boundaries.
    #[serde(default)]
    pub allowed_paths: Vec<String>,

    /// Subagents this agent can delegate tasks to.
    /// One ResourceSpec may resolve to N NodeIds on the Bus.
    #[serde(default)]
    pub subagents: Vec<ResourceSpec>,

    /// Teammates this agent can coordinate with.
    /// One ResourceSpec may resolve to N NodeIds on the Bus.
    #[serde(default)]
    pub teammates: Vec<ResourceSpec>,
}

impl AgentConfig {
    /// Create a new AgentConfig with all fields empty.
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
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ModelSpec, ResourceSpec, ToolPermission, ToolSpec};
    use serde_json;

    // ═══════════════════════════════════════════════════════════════
    // AgentConfig — 9 tests
    // ═══════════════════════════════════════════════════════════════

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
        let model = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-flash".into(),
            thinking_enabled: true,
            temperature: Some(0.7),
            max_output_tokens: Some(4096),
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
        let config = AgentConfig {
            system_prompt: "test".into(),
            ..AgentConfig::default()
        };
        assert_eq!(config, config.clone());
    }

    // [trait] PartialEq：相同字段相等
    #[test]
    fn agent_config_equality() {
        let a = AgentConfig {
            system_prompt: "prompt".into(),
            ..AgentConfig::default()
        };
        let b = AgentConfig {
            system_prompt: "prompt".into(),
            ..AgentConfig::default()
        };
        let c = AgentConfig {
            system_prompt: "different".into(),
            ..AgentConfig::default()
        };
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [序列化] 空 Config serde 往返：所有字段为空
    #[test]
    fn agent_config_empty_serialization_roundtrip() {
        let config = AgentConfig::new();
        let json = serde_json::to_string(&config).unwrap();
        let back: AgentConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(config, back);
        assert!(json.contains("system_prompt"));
        assert!(json.contains("models"));
        assert!(json.contains("tools"));
    }

    // [序列化] 含数据的完整往返
    #[test]
    fn agent_config_full_serialization_roundtrip() {
        let config = AgentConfig {
            system_prompt: "You are an assistant.".into(),
            models: vec![ModelSpec {
                provider: "deepseek".into(),
                model_name: "deepseek-flash".into(),
                thinking_enabled: true,
                temperature: Some(0.5),
                max_output_tokens: Some(8192),
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

    // [兼容] 空 JSON 对象反序列化：全部取默认值
    #[test]
    fn agent_config_deserialize_empty_json() {
        let json = "{}";
        let config: AgentConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config, AgentConfig::new());
    }

    // [兼容] 部分字段缺失不报错，缺失字段取默认值
    #[test]
    fn agent_config_deserialize_partial_json() {
        let json = r#"{"system_prompt": "hello", "allowed_paths": ["/x"]}"#;
        let config: AgentConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.system_prompt, "hello");
        assert_eq!(config.allowed_paths, vec!["/x"]);
        assert!(config.models.is_empty());
        assert!(config.tools.is_empty());
        assert!(config.subagents.is_empty());
        assert!(config.teammates.is_empty());
    }
}
