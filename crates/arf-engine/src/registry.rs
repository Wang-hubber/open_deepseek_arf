//! ResourceRegistry — 声明资源 → NodeId 静态映射（Phase 7 §4）。
//!
//! 替代 `collect_tools_from_routes` + `collect_skills_cached` + `find_tool_owner`。

use std::collections::HashMap;

use arf_bus::Bus;
use arf_core::{BusGraph, NodeId, NodeInfo, ToolSpec};

use crate::config::AgentConfig;
use crate::error::BuildError;

/// 声明 capabilities 的过滤模式。
#[derive(Clone, Debug)]
enum DeclaredFilter {
    /// "all" sentinel — 显式全取。
    All,
    /// 显式白名单。
    Subset(Vec<String>),
    /// capabilities 为 None 或空数组 — 全不取（安全默认）。
    None_,
}

#[derive(Debug)]
struct ResourceBinding {
    resource_name: String,
    node_id: NodeId,
    declared_filter: DeclaredFilter,
}

/// 声明资源的静态解析表。build 时冻结，运行时只读。
#[derive(Debug)]
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
            let filter = parse_declared_filter(&spec.capabilities, &spec.resource_name);

            // 按 filter 类型选择节点匹配策略：
            // - Subset(names): 找 node_type 匹配且至少一个 name 出现在节点
            //   tools/skills 中的节点（避免两条 spec 同时命中第一个 mcp 节点）。
            // - All / None_  : 找第一个 node_type 匹配的节点（保留旧行为）。
            let node = match &filter {
                DeclaredFilter::Subset(names) => snapshot
                    .nodes
                    .iter()
                    .find(|n| n.node_type == spec.node_type && node_has_any_of(n, names)),
                DeclaredFilter::All | DeclaredFilter::None_ => {
                    snapshot.nodes.iter().find(|n| n.node_type == spec.node_type)
                }
            }
            .ok_or_else(|| BuildError::MissingNodes {
                nodes: vec![format!("{}: node_type=\"{}\"", spec.resource_name, spec.node_type)],
            })?;

            let binding = ResourceBinding {
                resource_name: spec.resource_name.clone(),
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
                            providers: vec![existing.to_string(), spec.resource_name.clone()],
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

/// 判断节点的 capabilities 中是否包含 names 列表里的至少一个工具或技能名。
///
/// 用于 Subset filter 的节点匹配：只挑"能至少满足一个声明的工具/技能"的节点，
/// 而不是 `find()` 拿到的第一个 node_type 匹配节点。
fn node_has_any_of(node: &arf_core::NodeInfo, names: &[String]) -> bool {
    let tools: Vec<String> = node
        .capabilities
        .get("tools")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|t| t.get("name").and_then(|s| s.as_str()).map(String::from))
                .filter(|s| !s.is_empty())
                .collect()
        })
        .unwrap_or_default();
    let skills: Vec<String> = node
        .capabilities
        .get("skills")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|s| s.as_str().map(String::from))
                .filter(|s| !s.is_empty())
                .collect()
        })
        .unwrap_or_default();
    names
        .iter()
        .any(|n| tools.iter().any(|t| t == n) || skills.iter().any(|s| s == n))
}

fn resolve_model(decl: &AgentConfig, snapshot: &BusGraph) -> Result<NodeId, BuildError> {
    // F-008: nodes are sorted by node_id (Bus::graph), so iteration is
    // deterministic across processes — find() picks the first match.
    // F-007: additionally filter by `model_name` so cfg.model.model_name="x"
    // only matches nodes whose `capabilities.models` contain "x". Without
    // this, an unsupported model_name was silently routed to a node that
    // didn't actually serve it.
    let supports = |n: &NodeInfo| -> bool {
        // F-007 YELLOW: old model nodes (created before capabilities.models
        // was introduced) don't declare a `models` list. Treating "missing
        // list" as "supports nothing" silently excluded those nodes from
        // routing — old deployments would break. Default to `true` for
        // backward compat: missing list = "assumed supports all" (matching
        // pre-F-007 semantics). Nodes that want strict matching must declare
        // a non-empty `models` list.
        n.capabilities
            .get("models")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().any(|m| m.as_str() == Some(&decl.model.model_name)))
            .unwrap_or(true)
    };
    let node = snapshot
        .nodes
        .iter()
        .find(|n| {
            n.node_type == "model"
                && n.capabilities
                    .get("provider")
                    .and_then(|v| v.as_str())
                    == Some(&decl.model.provider)
                && supports(n)
        })
        .or_else(|| {
            // Fallback: provider matches but no node supports this model_name.
            // Return the first provider match so the caller can report a
            // clearer error (still prefer deterministic order).
            snapshot.nodes.iter().find(|n| {
                n.node_type == "model"
                    && n.capabilities
                        .get("provider")
                        .and_then(|v| v.as_str())
                        == Some(&decl.model.provider)
            })
        });
    match node {
        Some(n) if supports(n) => Ok(n.node_id.clone()),
        Some(_) => Err(BuildError::MissingNodes {
            nodes: vec![format!(
                "model: provider=\"{}\" model=\"{}\" (provider matched but no node supports this model)",
                decl.model.provider, decl.model.model_name
            )],
        }),
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
    use arf_agent::ModelDecl;
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
            // F-007: include `models` array so resolve_model matches.
            capabilities: serde_json::json!({
                "provider": "deepseek",
                "kind": "model",
                "models": ["deepseek-v4-flash"],
            }),
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
                resource_name: "files".into(),
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
                resource_name: "ghost".into(),
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

    // [方法] C4 F-007: resolve_model 按 model_name 选节点（多 node 同 provider）
    #[test]
    fn resolve_model_picks_by_model_name() {
        let node_a = NodeInfo {
            node_id: NodeId::new("model/openai-a"),
            node_type: "model".into(),
            capabilities: serde_json::json!({
                "provider": "openai",
                "models": ["qwen3.7"],
            }),
            online_since: 0,
        };
        let node_b = NodeInfo {
            node_id: NodeId::new("model/openai-b"),
            node_type: "model".into(),
            capabilities: serde_json::json!({
                "provider": "openai",
                "models": ["qwen3.5-turbo"],
            }),
            online_since: 0,
        };
        let decl = AgentConfig {
            model: ModelDecl {
                provider: "openai".into(),
                model_name: "qwen3.5-turbo".into(),
                ..Default::default()
            },
            ..Default::default()
        };
        // Only node_b supports the requested model_name → must pick node_b.
        let snap = test_snapshot(vec![node_a, node_b]);
        let reg = ResourceRegistry::build(&decl, &snap).unwrap();
        assert_eq!(reg.model_target().as_str(), "model/openai-b");
    }

    // [错误] C4 F-007: provider 匹配但 model_name 不被任一节点支持 → MissingNodes
    #[test]
    fn resolve_model_errors_on_unsupported_model() {
        let node = NodeInfo {
            node_id: NodeId::new("model/openai"),
            node_type: "model".into(),
            capabilities: serde_json::json!({
                "provider": "openai",
                "models": ["qwen3.7"],
            }),
            online_since: 0,
        };
        let decl = AgentConfig {
            model: ModelDecl {
                provider: "openai".into(),
                model_name: "qwen3.5-turbo".into(),
                ..Default::default()
            },
            ..Default::default()
        };
        let snap = test_snapshot(vec![node]);
        let err = ResourceRegistry::build(&decl, &snap).unwrap_err();
        match err {
            BuildError::MissingNodes { nodes } => {
                assert!(
                    nodes[0].contains("no node supports this model"),
                    "err msg should mention unsupported model, got: {}",
                    nodes[0]
                );
            }
            other => panic!("expected MissingNodes, got {other:?}"),
        }
    }

    // [冲突] 两个 resource 声明同一工具 → AmbiguousTool
    #[test]
    fn registry_build_ambiguous_tool_fails() {
        let common_tool = serde_json::json!([{"name": "read"}]);
        let decl = AgentConfig {
            resources: vec![
                ResourceSpec {
                    resource_name: "files".into(),
                    node_type: "mcp".into(),
                    capabilities: Some(serde_json::json!({"tools": ["read"]})),
                },
                ResourceSpec {
                    resource_name: "code".into(),
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
                resource_name: "files".into(),
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
                resource_name: "files".into(),
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

    // [回归] 两条 Subset spec 各自匹配能提供工具的 mcp 节点
//
// Bug 复现：之前两条 node_type="mcp" 的 spec 都命中第一个 mcp 节点，
// 导致第二条 spec 的 binding 覆盖第一条，且 filter 拒绝节点实际工具，
// tool_index 里没有 codetidy_json_format。
#[test]
fn registry_two_subset_specs_resolve_to_distinct_nodes() {
    let decl = AgentConfig {
        resources: vec![
            ResourceSpec {
                resource_name: "local".into(),
                node_type: "mcp".into(),
                capabilities: Some(serde_json::json!({"tools": ["get_time", "random_number"]})),
            },
            ResourceSpec {
                resource_name: "remote".into(),
                node_type: "mcp".into(),
                capabilities: Some(serde_json::json!({"tools": ["codetidy_json_format"]})),
            },
        ],
        ..Default::default()
    };
    let snapshot = test_snapshot(vec![
        model_node(),
        mcp_node("tools/local", serde_json::json!([
            {"name": "get_time", "description": "t", "params_schema": {}},
            {"name": "random_number", "description": "r", "params_schema": {}},
        ])),
        mcp_node("codetidy/remote", serde_json::json!([
            {"name": "codetidy_json_format", "description": "f", "params_schema": {}},
        ])),
    ]);
    let registry = ResourceRegistry::build(&decl, &snapshot).unwrap();

    assert_eq!(registry.owner_of_tool("get_time").unwrap().as_str(), "tools/local");
    assert_eq!(registry.owner_of_tool("random_number").unwrap().as_str(), "tools/local");
    assert_eq!(registry.owner_of_tool("codetidy_json_format").unwrap().as_str(), "codetidy/remote");

    // 两个 mcp binding 都应保留（不再被覆盖）
    assert_eq!(registry.mcp_nodes.len(), 2);
    assert!(registry.mcp_nodes.contains_key(&NodeId::new("tools/local")));
    assert!(registry.mcp_nodes.contains_key(&NodeId::new("codetidy/remote")));
}

// [错误] Subset filter 没有节点能提供任一指定工具 → MissingNode
#[test]
fn registry_subset_no_matching_node_fails() {
    let decl = AgentConfig {
        resources: vec![ResourceSpec {
            resource_name: "ghost".into(),
            node_type: "mcp".into(),
            capabilities: Some(serde_json::json!({"tools": ["nonexistent_tool"]})),
        }],
        ..Default::default()
    };
    let snapshot = test_snapshot(vec![
        model_node(),
        mcp_node("mcp/files", serde_json::json!([
            {"name": "read", "description": "r", "params_schema": {}},
        ])),
    ]);
    assert!(matches!(
        ResourceRegistry::build(&decl, &snapshot),
        Err(BuildError::MissingNodes { .. })
    ));
}

// [兼容] capabilities=None 走"第一个匹配节点"路径（保留旧行为）
#[test]
fn registry_none_filter_picks_first_node() {
    let decl = AgentConfig {
        resources: vec![ResourceSpec {
            resource_name: "ambiguous".into(),
            node_type: "mcp".into(),
            capabilities: None,
        }],
        ..Default::default()
    };
    let snapshot = test_snapshot(vec![
        model_node(),
        mcp_node("mcp/a", serde_json::json!([{"name": "x", "description": "x", "params_schema": {}}])),
        mcp_node("mcp/b", serde_json::json!([{"name": "y", "description": "y", "params_schema": {}}])),
    ]);
    let registry = ResourceRegistry::build(&decl, &snapshot).unwrap();
    // build 成功但不注册任何 tool（None filter 拒绝所有）
    assert!(registry.owner_of_tool("x").is_none());
    assert!(registry.owner_of_tool("y").is_none());
    assert_eq!(registry.mcp_nodes.len(), 1);
}

// [兼容] capabilities={"tools":"all"} 走"第一个匹配节点 + 注册所有"（保留旧行为）
#[test]
fn registry_all_sentinel_picks_first_node_with_all_tools() {
    let decl = AgentConfig {
        resources: vec![ResourceSpec {
            resource_name: "all_mcp".into(),
            node_type: "mcp".into(),
            capabilities: Some(serde_json::json!({"tools": "all"})),
        }],
        ..Default::default()
    };
    let snapshot = test_snapshot(vec![
        model_node(),
        mcp_node("mcp/a", serde_json::json!([
            {"name": "alpha", "description": "a", "params_schema": {}},
        ])),
        mcp_node("mcp/b", serde_json::json!([
            {"name": "beta", "description": "b", "params_schema": {}},
        ])),
    ]);
    let registry = ResourceRegistry::build(&decl, &snapshot).unwrap();
    // All filter 取第一个节点的全部 tool
    assert_eq!(registry.owner_of_tool("alpha").unwrap().as_str(), "mcp/a");
    assert!(registry.owner_of_tool("beta").is_none());
}

// [查询] owner_of_tool 返回正确的 NodeId
    #[test]
    fn registry_owner_of_tool_returns_correct_node() {
        let decl = AgentConfig {
            resources: vec![ResourceSpec {
                resource_name: "files".into(),
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
