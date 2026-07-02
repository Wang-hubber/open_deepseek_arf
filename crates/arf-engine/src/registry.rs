//! ResourceRegistry — 声明资源 → NodeId 静态映射（Phase 7 §4）。
//!
//! 替代 `collect_tools_from_routes` + `collect_skills_cached` + `find_tool_owner`。

use std::collections::HashMap;

use arf_bus::Bus;
use arf_core::{BusGraph, NodeId, ToolSpec};

use crate::config::AgentConfig;
use crate::error::BuildError;

/// 声明 capabilities 的过滤模式。
#[derive(Clone)]
enum DeclaredFilter {
    /// "all" sentinel — 显式全取。
    All,
    /// 显式白名单。
    Subset(Vec<String>),
    /// capabilities 为 None 或空数组 — 全不取（安全默认）。
    None_,
}

struct ResourceBinding {
    resource_name: String,
    node_id: NodeId,
    declared_filter: DeclaredFilter,
}

/// 声明资源的静态解析表。build 时冻结，运行时只读。
pub(crate) struct ResourceRegistry {
    /// model 节点。
    model: NodeId,
    /// MCP 节点（node_type="mcp" 或 "mcp/pool"）。
    mcp_nodes: HashMap<NodeId, ResourceBinding>,
    /// 自定义节点（非 model/mcp）。
    custom_nodes: HashMap<NodeId, ResourceBinding>,
    /// 工具名 → owner NodeId（build 时从 MCP 节点实际 tools 计算，防重名）。
    tool_index: HashMap<String, NodeId>,
    /// Skills 缓存：(hash, content)。
    skills_cache: std::sync::Mutex<(u64, String)>,
}

impl ResourceRegistry {
    /// 从声明 + Bus 拓扑 snapshot 构建。
    pub(crate) fn build(decl: &AgentConfig, snapshot: &BusGraph) -> Result<Self, BuildError> {
        // 1. 解析 model
        let model = resolve_model(decl, snapshot)?;

        // 2. 解析 resources
        let mut mcp_nodes = HashMap::new();
        let mut custom_nodes = HashMap::new();
        let mut tool_index: HashMap<String, NodeId> = HashMap::new();

        for spec in &decl.resources {
            let node = snapshot
                .nodes
                .iter()
                .find(|n| n.node_type == spec.node_type)
                .ok_or_else(|| BuildError::MissingNodes {
                    nodes: vec![format!("{}: node_type=\"{}\"", spec.name, spec.node_type)],
                })?;

            let filter = parse_declared_filter(&spec.capabilities, &spec.name);

            let binding = ResourceBinding {
                resource_name: spec.name.clone(),
                node_id: node.node_id.clone(),
                declared_filter: filter.clone(),
            };

            if spec.node_type == "mcp" || spec.node_type.starts_with("mcp/") {
                // 从 MCP 节点的实际 capabilities.tools 读取工具列表，
                // 用 declared_filter 过滤后填充 tool_index
                let node_actual_tools: Vec<String> = node
                    .capabilities
                    .get("tools")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|t| {
                                t.get("name").and_then(|s| s.as_str()).map(String::from)
                            })
                            .filter(|s| !s.is_empty())
                            .collect()
                    })
                    .unwrap_or_default();

                for tname in &node_actual_tools {
                    if !filter.accepts(tname) {
                        continue;
                    }
                    if let Some(existing) = tool_index.get(tname) {
                        return Err(BuildError::AmbiguousTool {
                            tool: tname.clone(),
                            providers: vec![existing.to_string(), spec.name.clone()],
                        });
                    }
                    tool_index.insert(tname.clone(), node.node_id.clone());
                }
                mcp_nodes.insert(node.node_id.clone(), binding);
            } else {
                custom_nodes.insert(node.node_id.clone(), binding);
            }
        }

        Ok(Self {
            model,
            mcp_nodes,
            custom_nodes,
            tool_index,
            skills_cache: std::sync::Mutex::new((0, String::new())),
        })
    }

    /// model_call 的 to 目标。
    pub(crate) fn model_target(&self) -> NodeId {
        self.model.clone()
    }

    /// 查询工具所属 MCP 节点的 NodeId。O(1)。
    pub(crate) fn owner_of_tool(&self, tool_name: &str) -> Option<NodeId> {
        self.tool_index.get(tool_name).cloned()
    }

    /// 收集所有声明工具 → ToolSpec（用于 model_call.tools）。
    pub(crate) fn tools_for_model(&self, bus: &Bus) -> Vec<ToolSpec> {
        let graph = bus.graph();
        let mut specs = Vec::new();
        for (node_id, binding) in &self.mcp_nodes {
            let Some(node) = graph.nodes.iter().find(|n| &n.node_id == node_id) else {
                continue;
            };
            let Some(arr) = node.capabilities.get("tools").and_then(|v| v.as_array()) else {
                continue;
            };
            for t in arr {
                let name = t.get("name").and_then(|v| v.as_str()).unwrap_or("");
                if name.is_empty() {
                    continue;
                }
                if !binding.declared_filter.accepts(name) {
                    continue;
                }
                let description = t
                    .get("description")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let params = t.get("params_schema").cloned().unwrap_or(serde_json::json!({}));
                specs.push(ToolSpec::new(name, description, params));
            }
        }
        specs
    }

    /// 收集声明 skills 的 system prefix 文本（hash-cache）。
    pub(crate) fn skills_text(&self, bus: &Bus) -> String {
        let graph = bus.graph();
        let mut pairs: Vec<(String, String)> = Vec::new();
        for (node_id, binding) in &self.mcp_nodes {
            let Some(node) = graph.nodes.iter().find(|n| &n.node_id == node_id) else {
                continue;
            };
            let Some(arr) = node.capabilities.get("skills").and_then(|v| v.as_array()) else {
                continue;
            };
            for s in arr {
                let sname = s.as_str().unwrap_or("");
                if sname.is_empty() || !binding.declared_filter.accepts(sname) {
                    continue;
                }
                pairs.push((node_id.to_string(), sname.to_string()));
            }
        }
        pairs.sort();

        let mut hash: u64 = pairs.len() as u64;
        for (nid, sn) in &pairs {
            hash = hash.wrapping_mul(31).wrapping_add(nid.len() as u64);
            hash = hash.wrapping_mul(31).wrapping_add(sn.len() as u64);
        }

        let mut cache = self.skills_cache.lock().unwrap();
        if cache.0 == hash && !cache.1.is_empty() {
            return cache.1.clone();
        }
        let content = if pairs.is_empty() {
            String::new()
        } else {
            let items: Vec<String> = pairs
                .iter()
                .map(|(nid, sn)| format!("- {sn} (from {nid})"))
                .collect();
            format!("Available skills:\n{}", items.join("\n"))
        };
        *cache = (hash, content.clone());
        content
    }

    /// 查询自定义节点的 NodeId。
    pub(crate) fn owner_of_custom(&self, msg_type: &str) -> Option<NodeId> {
        self.custom_nodes
            .values()
            .find(|b| b.resource_name == msg_type)
            .map(|b| b.node_id.clone())
    }
}

// ── helpers ──

fn resolve_model(decl: &AgentConfig, snapshot: &BusGraph) -> Result<NodeId, BuildError> {
    let node = snapshot.nodes.iter().find(|n| {
        n.node_type == "model"
            && n.capabilities
                .get("provider")
                .and_then(|v| v.as_str())
                == Some(&decl.model.provider)
    });
    match node {
        Some(n) => Ok(n.node_id.clone()),
        None => Err(BuildError::MissingNodes {
            nodes: vec![format!(
                "model: provider=\"{}\" model=\"{}\"",
                decl.model.provider, decl.model.model_name
            )],
        }),
    }
}

fn parse_declared_filter(
    capabilities: &Option<serde_json::Value>,
    resource_name: &str,
) -> DeclaredFilter {
    let caps = match capabilities {
        Some(c) => c,
        None => {
            log::warn!(
                "Resource '{}' declared no capabilities filter — no tools/skills will be registered. \
                 Add explicit capabilities: {{\"tools\": [...], \"skills\": [...]}} or use \"all\".",
                resource_name
            );
            return DeclaredFilter::None_;
        }
    };

    let mut all_names = Vec::new();
    for key in &["tools", "skills"] {
        if let Some(arr) = caps.get(key).and_then(|v| v.as_array()) {
            for item in arr {
                match item.as_str() {
                    Some("all") => return DeclaredFilter::All,
                    Some(s) if !s.is_empty() => all_names.push(s.to_string()),
                    _ => {}
                }
            }
        } else if let Some(s) = caps.get(key).and_then(|v| v.as_str()) {
            if s == "all" {
                return DeclaredFilter::All;
            }
        }
    }

    if all_names.is_empty() {
        log::warn!(
            "Resource '{}' declared empty capabilities — no tools/skills will be registered. \
             Use \"all\" to take everything, or list specific names.",
            resource_name
        );
        DeclaredFilter::None_
    } else {
        DeclaredFilter::Subset(all_names)
    }
}

impl DeclaredFilter {
    fn accepts(&self, name: &str) -> bool {
        match self {
            DeclaredFilter::All => true,
            DeclaredFilter::Subset(names) => names.iter().any(|n| n == name),
            DeclaredFilter::None_ => false,
        }
    }
}

// ── tests ──

#[cfg(test)]
mod tests {
    use super::*;
    use arf_agent::ResourceSpec;
    use arf_core::NodeInfo;

    fn test_snapshot(nodes: Vec<NodeInfo>) -> BusGraph {
        BusGraph {
            nodes,
            message_count: 0,
            uptime_ms: 0,
        }
    }

    fn model_node() -> NodeInfo {
        NodeInfo {
            node_id: NodeId::new("model/deepseek"),
            node_type: "model".into(),
            capabilities: serde_json::json!({"provider": "deepseek", "kind": "model"}),
            online_since: 0,
        }
    }

    fn mcp_node(id: &str, tools: serde_json::Value) -> NodeInfo {
        NodeInfo {
            node_id: NodeId::new(id),
            node_type: "mcp".into(),
            capabilities: serde_json::json!({"tools": tools}),
            online_since: 0,
        }
    }

    // [构造] 合法声明 → Registry 构建成功，model + tool 正确
    #[test]
    fn registry_build_all_resources_resolved() {
        let decl = AgentConfig {
            resources: vec![ResourceSpec {
                name: "files".into(),
                node_type: "mcp".into(),
                capabilities: Some(serde_json::json!({"tools": ["read"]})),
            }],
            ..Default::default()
        };
        let snapshot = test_snapshot(vec![
            model_node(),
            mcp_node(
                "mcp/files",
                serde_json::json!([{"name": "read", "description": "read file", "params_schema": {}}]),
            ),
        ]);
        let registry = ResourceRegistry::build(&decl, &snapshot).unwrap();
        assert_eq!(registry.model_target().as_str(), "model/deepseek");
        assert_eq!(
            registry.owner_of_tool("read").unwrap().as_str(),
            "mcp/files"
        );
    }

    // [错误] model 不在线 → MissingNode
    #[test]
    fn registry_build_missing_model_fails() {
        let decl = AgentConfig::default();
        let snapshot = test_snapshot(vec![]);
        assert!(matches!(
            ResourceRegistry::build(&decl, &snapshot),
            Err(BuildError::MissingNodes { .. })
        ));
    }

    // [错误] 声明的 mcp 不在线 → MissingNode
    #[test]
    fn registry_build_missing_mcp_fails() {
        let decl = AgentConfig {
            resources: vec![ResourceSpec {
                name: "ghost".into(),
                node_type: "mcp".into(),
                capabilities: None,
            }],
            ..Default::default()
        };
        let snapshot = test_snapshot(vec![model_node()]);
        assert!(matches!(
            ResourceRegistry::build(&decl, &snapshot),
            Err(BuildError::MissingNodes { .. })
        ));
    }

    // [冲突] 两个 resource 声明同一工具 → AmbiguousTool
    #[test]
    fn registry_build_ambiguous_tool_fails() {
        let common_tool = serde_json::json!([{"name": "read"}]);
        let decl = AgentConfig {
            resources: vec![
                ResourceSpec {
                    name: "files".into(),
                    node_type: "mcp".into(),
                    capabilities: Some(serde_json::json!({"tools": ["read"]})),
                },
                ResourceSpec {
                    name: "code".into(),
                    node_type: "mcp".into(),
                    capabilities: Some(serde_json::json!({"tools": ["read"]})),
                },
            ],
            ..Default::default()
        };
        let snapshot = test_snapshot(vec![
            model_node(),
            mcp_node("mcp/files", common_tool.clone()),
            mcp_node("mcp/code", common_tool),
        ]);
        assert!(matches!(
            ResourceRegistry::build(&decl, &snapshot),
            Err(BuildError::AmbiguousTool { .. })
        ));
    }

    // [安全] capabilities=None → 全不取
    #[test]
    fn registry_build_none_capabilities_rejects_all() {
        let decl = AgentConfig {
            resources: vec![ResourceSpec {
                name: "files".into(),
                node_type: "mcp".into(),
                capabilities: None,
            }],
            ..Default::default()
        };
        let snapshot = test_snapshot(vec![
            model_node(),
            mcp_node("mcp/files", serde_json::json!([{"name": "read"}])),
        ]);
        let registry = ResourceRegistry::build(&decl, &snapshot).unwrap();
        assert!(registry.owner_of_tool("read").is_none());
    }

    // [显式] capabilities={"tools":"all"} → 全取，注册所有工具
    #[test]
    fn registry_build_all_sentinel() {
        let decl = AgentConfig {
            resources: vec![ResourceSpec {
                name: "files".into(),
                node_type: "mcp".into(),
                capabilities: Some(serde_json::json!({"tools": "all"})),
            }],
            ..Default::default()
        };
        let snapshot = test_snapshot(vec![
            model_node(),
            mcp_node(
                "mcp/files",
                serde_json::json!([
                    {"name": "read", "description": "r", "params_schema": {}},
                    {"name": "write", "description": "w", "params_schema": {}},
                ]),
            ),
        ]);
        let registry = ResourceRegistry::build(&decl, &snapshot).unwrap();
        assert!(registry.owner_of_tool("read").is_some());
        assert!(registry.owner_of_tool("write").is_some());
    }

    // [查询] owner_of_tool 返回正确的 NodeId
    #[test]
    fn registry_owner_of_tool_returns_correct_node() {
        let decl = AgentConfig {
            resources: vec![ResourceSpec {
                name: "files".into(),
                node_type: "mcp".into(),
                capabilities: Some(serde_json::json!({"tools": ["read"]})),
            }],
            ..Default::default()
        };
        let snapshot = test_snapshot(vec![
            model_node(),
            mcp_node("mcp/files", serde_json::json!([{"name": "read"}])),
        ]);
        let registry = ResourceRegistry::build(&decl, &snapshot).unwrap();
        assert_eq!(
            registry.owner_of_tool("read").unwrap().as_str(),
            "mcp/files"
        );
        assert!(registry.owner_of_tool("nonexistent").is_none());
    }
}
