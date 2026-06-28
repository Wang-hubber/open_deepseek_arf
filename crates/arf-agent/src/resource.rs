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
    /// Agent-given alias for this resource.
    ///
    /// Used in logs, error messages, and as a human-readable reference.
    /// Examples: `"primary_fs"`, `"code_reviewer"`, `"web_searcher"`.
    pub name: String,

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
            name: "primary_fs".into(),
            node_type: "mcp".into(),
            capabilities: Some(serde_json::json!({"resources": ["tool/read", "tool/write"]})),
        };
        assert_eq!(spec.name, "primary_fs");
        assert_eq!(spec.node_type, "mcp");
        let caps = spec.capabilities.unwrap();
        assert_eq!(caps["resources"][0], "tool/read");
        assert_eq!(caps["resources"][1], "tool/write");
    }

    // [构造] capabilities 为 None：匹配所有同 node_type 节点
    #[test]
    fn resource_spec_no_capabilities() {
        let spec = ResourceSpec {
            name: "any_fs".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        assert_eq!(spec.name, "any_fs");
        assert!(spec.capabilities.is_none());
    }

    // [边界] name 为空字符串：合法（Engine 用空串不 panic）
    #[test]
    fn resource_spec_empty_name() {
        let spec = ResourceSpec {
            name: "".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        assert_eq!(spec.name, "");
    }

    // [边界] capabilities 为 None 时不序列化到 JSON
    #[test]
    fn resource_spec_capabilities_none_skipped() {
        let spec = ResourceSpec {
            name: "x".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("capabilities"));
    }

    // [边界] 最小合法 JSON：仅 name + node_type，capabilities 缺省
    #[test]
    fn resource_spec_minimal_json() {
        let json = r#"{"name":"code_reviewer","node_type":"agent/subagent"}"#;
        let spec: ResourceSpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.name, "code_reviewer");
        assert_eq!(spec.node_type, "agent/subagent");
        assert!(spec.capabilities.is_none());
    }

    // [trait] Clone：克隆后相等
    #[test]
    fn resource_spec_clone() {
        let spec = ResourceSpec {
            name: "s".into(),
            node_type: "t".into(),
            capabilities: Some(serde_json::json!({"k": "v"})),
        };
        assert_eq!(spec, spec.clone());
    }

    // [trait] PartialEq：相同字段相等，不同 name 不等
    #[test]
    fn resource_spec_equality() {
        let a = ResourceSpec {
            name: "a".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        let b = ResourceSpec {
            name: "a".into(),
            node_type: "mcp".into(),
            capabilities: None,
        };
        let c = ResourceSpec {
            name: "c".into(),
            ..a.clone()
        };
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [序列化] 全字段 serde 往返
    #[test]
    fn resource_spec_serialization_roundtrip_full() {
        let spec = ResourceSpec {
            name: "web_searcher".into(),
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

    // [序列化] 最简 spec 往返：仅 name + node_type
    #[test]
    fn resource_spec_serialization_roundtrip_minimal() {
        let spec = ResourceSpec {
            name: "minimal".into(),
            node_type: "agent/teammate".into(),
            capabilities: None,
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ResourceSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }
}
