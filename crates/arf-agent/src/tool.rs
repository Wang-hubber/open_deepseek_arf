//! Agent tool declaration — tool name, permission, parameter constraints.

use serde::{Deserialize, Serialize};

/// Permission level for an agent tool.
///
/// Controls whether the tool runs automatically, requires user approval,
/// or is blocked entirely. Engine enforces this at runtime.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ToolPermission {
    /// Tool runs without asking the user.
    Allow,
    /// Tool must ask the user before running.
    Ask,
    /// Tool is blocked — Engine rejects any call.
    Deny,
}

/// A tool this agent may call, with permission constraints.
///
/// `ToolSpec` declares a tool by its logical name (matching the tool name
/// registered by an MCP node on the Bus). It does not reference any Bus
/// NodeId — Engine resolves the tool name to concrete MCP nodes at runtime.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolSpec {
    /// Tool name as registered on the Bus (e.g., `"read_file"`, `"web_search"`).
    pub name: String,

    /// Permission level for this tool.
    pub permission: ToolPermission,

    /// Optional parameter filter/constraints.
    /// E.g., `{"paths": ["/workspace/*"]}` to restrict file tool access.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parameter_filter: Option<serde_json::Value>,

    /// Natural-language description for the model's function calling.
    /// If `None`, the tool's Bus-registered description is used as fallback.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,

    /// JSON Schema for the tool's parameters (for function calling).
    /// If `None`, the tool's Bus-registered schema is used as fallback.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parameters: Option<serde_json::Value>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json;

    // ═══════════════════════════════════════════════════════════════
    // ToolPermission — 6 tests
    // ═══════════════════════════════════════════════════════════════

    // [覆盖] 三种变体均可构造
    #[test]
    fn tool_permission_all_variants_construct() {
        let _ = ToolPermission::Allow;
        let _ = ToolPermission::Ask;
        let _ = ToolPermission::Deny;
    }

    // [trait] PartialEq：相同变体相等，不同不等
    #[test]
    fn tool_permission_equality() {
        assert_eq!(ToolPermission::Allow, ToolPermission::Allow);
        assert_eq!(ToolPermission::Ask, ToolPermission::Ask);
        assert_ne!(ToolPermission::Allow, ToolPermission::Deny);
        assert_ne!(ToolPermission::Ask, ToolPermission::Deny);
    }

    // [trait] Clone：克隆后与原值相等
    #[test]
    fn tool_permission_clone() {
        assert_eq!(ToolPermission::Allow, ToolPermission::Allow.clone());
        assert_eq!(ToolPermission::Ask, ToolPermission::Ask.clone());
        assert_eq!(ToolPermission::Deny, ToolPermission::Deny.clone());
    }

    // [序列化] Allow — JSON 字符串 "Allow" 往返
    #[test]
    fn tool_permission_serialization_allow() {
        let json = serde_json::to_string(&ToolPermission::Allow).unwrap();
        assert_eq!(json, r#""Allow""#);
        let back: ToolPermission = serde_json::from_str(&json).unwrap();
        assert_eq!(back, ToolPermission::Allow);
    }

    // [序列化] Ask / Deny 变体往返
    #[test]
    fn tool_permission_serialization_ask_deny() {
        for perm in [ToolPermission::Ask, ToolPermission::Deny] {
            let json = serde_json::to_string(&perm).unwrap();
            let back: ToolPermission = serde_json::from_str(&json).unwrap();
            assert_eq!(perm, back);
        }
    }

    // [兼容] 未知变体反序列化报错（拒绝未知权限）
    #[test]
    fn tool_permission_unknown_variant_error() {
        let result: Result<ToolPermission, _> = serde_json::from_str(r#""Unknown""#);
        assert!(result.is_err());
    }

    // ═══════════════════════════════════════════════════════════════
    // ToolSpec — 8 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] 所有字段显式赋值可读
    #[test]
    fn tool_spec_all_fields() {
        let spec = ToolSpec {
            name: "read_file".into(),
            permission: ToolPermission::Ask,
            parameter_filter: Some(serde_json::json!({"paths": ["/workspace/*"]})),
            description: Some("Read a file from the workspace".into()),
            parameters: Some(serde_json::json!({"type": "object", "properties": {}})),
        };
        assert_eq!(spec.name, "read_file");
        assert_eq!(spec.permission, ToolPermission::Ask);
        assert!(spec.parameter_filter.is_some());
        assert!(spec.description.is_some());
        assert!(spec.parameters.is_some());
    }

    // [构造] 仅有 name + permission 的最简 ToolSpec
    #[test]
    fn tool_spec_minimal() {
        let spec = ToolSpec {
            name: "search".into(),
            permission: ToolPermission::Allow,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        assert_eq!(spec.name, "search");
        assert_eq!(spec.permission, ToolPermission::Allow);
    }

    // [边界] 可选字段为 None 时不序列化到 JSON
    #[test]
    fn tool_spec_optionals_skipped() {
        let spec = ToolSpec {
            name: "run".into(),
            permission: ToolPermission::Deny,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("parameter_filter"));
        assert!(!json.contains("description"));
        assert!(!json.contains("parameters"));
    }

    // [权限] Allow 变体：工具自动执行
    #[test]
    fn tool_spec_permission_allow() {
        let spec = ToolSpec {
            name: "auto_tool".into(),
            permission: ToolPermission::Allow,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        assert_eq!(spec.permission, ToolPermission::Allow);
    }

    // [权限] Deny 变体：工具被禁止
    #[test]
    fn tool_spec_permission_deny() {
        let spec = ToolSpec {
            name: "blocked_tool".into(),
            permission: ToolPermission::Deny,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        assert_eq!(spec.permission, ToolPermission::Deny);
    }

    // [trait] Clone：克隆后相等
    #[test]
    fn tool_spec_clone() {
        let spec = ToolSpec {
            name: "t".into(),
            permission: ToolPermission::Ask,
            parameter_filter: Some(serde_json::json!({"x": 1})),
            description: Some("desc".into()),
            parameters: None,
        };
        assert_eq!(spec, spec.clone());
    }

    // [序列化] 全字段 serde 往返
    #[test]
    fn tool_spec_serialization_roundtrip_full() {
        let spec = ToolSpec {
            name: "web_search".into(),
            permission: ToolPermission::Ask,
            parameter_filter: Some(serde_json::json!({"domains": ["wikipedia.org"]})),
            description: Some("Search the web".into()),
            parameters: Some(serde_json::json!({"type": "object"})),
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ToolSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [序列化] 最简 spec 往返：仅 name + permission
    #[test]
    fn tool_spec_serialization_roundtrip_minimal() {
        let spec = ToolSpec {
            name: "minimal".into(),
            permission: ToolPermission::Allow,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ToolSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }
}
