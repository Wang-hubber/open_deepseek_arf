//! Agent resource declaration — logical resource requirements for discovery.

use serde::{Deserialize, Serialize};

/// Declares a logical resource dependency.
///
/// Agent says "I need a filesystem". Engine discovers N matching nodes
/// on the Bus and registers all of them. At runtime, Engine selects
/// the first online node.
///
/// 1:N mapping is inherent: one logical need → multiple concrete NodeIds.
/// This is NOT a 1:1 binding — it's a discovery filter.
///
/// `ResourceSpec` knows nothing about the Bus or NodeIds. It only speaks
/// logical names and capability matchers. Engine (Phase 4) performs the
/// actual resolution.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ResourceSpec {
    /// Agent-given alias for this logical resource.
    ///
    /// **NOT a NodeId or NodeName.** This is purely a human-readable label
    /// that the agent (or app author) gives to "what I want from this
    /// resource". Used in:
    /// - log messages
    /// - `BuildError::MissingNodes` / `BuildError::AmbiguousTool` payloads
    /// - `ResourceBinding.resource_name` (informational)
    ///
    /// The actual node matching is done by `node_type` (+ optional
    /// `capabilities` filter). Two `ResourceSpec`s may share the same
    /// `resource_name` without conflict; what matters is the resolved
    /// tools/skills they bring in.
    ///
    /// JSON wire form uses `"resource_name"`; the legacy `"name"` key is
    /// still accepted as a serde alias for backward compatibility.
    ///
    /// Examples: `"time_keeper"`, `"json_formatter"`, `"code_reviewer"`.
    #[serde(alias = "name")]
    pub resource_name: String,

    /// Expected `node_type` on the Bus when Engine does discovery.
    ///
    /// Common values:
    /// - `"mcp"` — tool/resource provider nodes
    /// - `"agent/subagent"` — subagent nodes
    /// - `"agent/teammate"` — teammate nodes
    pub node_type: String,

    /// Optional capabilities matcher.
    ///
    /// Engine filters discovery results by matching each node's
    /// `node_info.capabilities` against this value. The match is a
    /// subset check: a node matches if its capabilities contain all
    /// keys/values specified here.
    ///
    /// Examples:
    /// - `{"resources": ["tool/read", "tool/write"]}` — only MCP nodes
    ///   that provide file I/O tools
    /// - `None` — matches any node of the given `node_type`
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capabilities: Option<serde_json::Value>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json;

    // ═══════════════════════════════════════════════════════════════
    // ResourceSpec — 9 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] 所有字段显式赋值可读
    #[test]
    fn resource_spec_all_fields() {
        let spec = ResourceSpec {
            resource_name: "primary_fs".into(),
            node_type: "mcp".into(),
            capabilities: Some(serde_json::json!({"resources": ["tool/read", "tool/write"]})),
        };
        assert_eq!(spec.resource_name, "primary_fs");
        assert_eq!(spec.node_type, "mcp");
        let caps = spec.capabilities.unwrap();
        assert_eq!(caps["resources"][0], "tool/read");
        assert_eq!(caps["resources"][1], "tool/write");
    }

    // [构造] capabilities 为 None：匹配所有同 node_type 节点
    #[test]
    fn resource_spec_no_capabilities() {
        let spec = ResourceSpec {
            resource_name: "any_fs".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        assert_eq!(spec.resource_name, "any_fs");
        assert!(spec.capabilities.is_none());
    }

    // [边界] resource_name 为空字符串：合法（Engine 用空串不 panic）
    #[test]
    fn resource_spec_empty_name() {
        let spec = ResourceSpec {
            resource_name: "".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        assert_eq!(spec.resource_name, "");
    }

    // [边界] capabilities 为 None 时不序列化到 JSON
    #[test]
    fn resource_spec_capabilities_none_skipped() {
        let spec = ResourceSpec {
            resource_name: "x".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("capabilities"));
    }

    // [边界] 最小合法 JSON：仅 resource_name + node_type，capabilities 缺省
    #[test]
    fn resource_spec_minimal_json() {
        let json = r#"{"name":"code_reviewer","node_type":"agent/subagent"}"#;
        let spec: ResourceSpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.resource_name, "code_reviewer");
        assert_eq!(spec.node_type, "agent/subagent");
        assert!(spec.capabilities.is_none());
    }

    // [trait] Clone：克隆后相等
    #[test]
    fn resource_spec_clone() {
        let spec = ResourceSpec {
            resource_name: "s".into(),
            node_type: "t".into(),
            capabilities: Some(serde_json::json!({"k": "v"})),
        };
        assert_eq!(spec, spec.clone());
    }

    // [trait] PartialEq：相同字段相等，不同 resource_name 不等
    #[test]
    fn resource_spec_equality() {
        let a = ResourceSpec {
            resource_name: "a".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        let b = ResourceSpec {
            resource_name: "a".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        let c = ResourceSpec {
            resource_name: "c".into(),
            ..a.clone()
        };
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [序列化] 全字段 serde 往返
    #[test]
    fn resource_spec_serialization_roundtrip_full() {
        let spec = ResourceSpec {
            resource_name: "web_searcher".into(),
            node_type: "mcp".into(),
            capabilities: Some(serde_json::json!({
                "resources": ["tool/web_search"],
                "version": "1.0"
            })),
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ResourceSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [序列化] 最简 spec 往返：仅 resource_name + node_type
    #[test]
    fn resource_spec_serialization_roundtrip_minimal() {
        let spec = ResourceSpec {
            resource_name: "minimal".into(),
            node_type: "agent/teammate".into(),
            capabilities: None,
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ResourceSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [兼容] JSON 用旧字段名 "name" 仍可反序列化（serde alias）；
    // 序列化时输出新字段名 "resource_name"。
    #[test]
    fn resource_spec_json_accepts_name_alias() {
        let legacy_json = r#"{"name":"legacy_alias","node_type":"mcp"}"#;
        let spec: ResourceSpec = serde_json::from_str(legacy_json).unwrap();
        assert_eq!(spec.resource_name, "legacy_alias");

        let serialized = serde_json::to_string(&spec).unwrap();
        assert!(serialized.contains("\"resource_name\""), "new field name should serialize: {serialized}");
        assert!(!serialized.contains("\"name\""), "legacy key should not appear: {serialized}");
    }
}
