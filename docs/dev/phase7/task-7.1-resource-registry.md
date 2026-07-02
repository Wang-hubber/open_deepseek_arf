# 任务 7.1：ResourceRegistry — 统一资源声明与路由

> Phase 7 — E2E 第一项任务
> 父文档：`docs/dev/phase7/phase7-resource-registry-design.md`
> 前置：Phase 6 全部 Engine 核心实现 ✅

## 设计思路

将 Engine 内散落的 `collect_tools_from_routes` / `collect_skills_cached` / `find_tool_owner` 合并为一个 `ResourceRegistry`，同时复活 `arf-agent` crate 的声明层角色。AgentConfig 从 14 字段瘦身到 `model: ModelDecl` + `resources: Vec<ResourceSpec>` + `engine: EngineConfig` 三层嵌套。

### 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Engine 投递模型 | `bus.publish(msg.with_to(vec![target]))` broadcast + filter | trace 完整性——online_nodes / matching_nodes 均在 receipt 中 |
| 在线状态 | Bus 是单一可信源，Engine 不维护 online 集合 | 避免状态漂移；offline 走 `SendError::NodeOffline` 即时 fail |
| Skills 发现 | 显式 `ResourceSpec.capabilities` | 废弃 `kind=skill` 自动收集；Agent 明确声明需求 |
| capabilities 缺省 | `None` → build warning + 全取；`"all"` sentinel → 无 warning 全取 | 兼容但不鼓励；教学路径从第一天就用显式声明 |
| 工具名冲突 | 重名 → `BuildError::AmbiguousTool` | 构建时 fail-fast，不等到模型调用才发现 |
| 资源分类 | `node_type` 字符串区分 | 不引入 enum variant；与 `NodeInfo.node_type` 字段天然对齐 |

### 不在 7.1 范围

- Subagent 组合模型（`node_type="agent/subagent"` 声明入口预留，Engine 侧 subagent call 协议另开）
- NodePool 的并发 worker 管理（`node_type="mcp/pool"` 声明入口预留）
- Session.resume API
- 取消传播

---

## 代码实现

### 切面 1：`crates/arf-agent/src/model.rs` — ModelSpec → ModelDecl

ModelSpec 是 Phase 3 的设计，缺少 `endpoint` / `api_key_env` 字段。扩展为 `ModelDecl`，加入 Provider 连接所需信息。

```rust
//! Agent model declaration — provider, model, endpoint, inference parameters.

use serde::{Deserialize, Serialize};

/// A single model declaration.
///
/// `ModelDecl` is a pure data declaration. It uses logical names
/// (`provider` + `model_name`) and does not reference any Bus NodeId.
/// Engine resolves it to a concrete `node_type="model"` node at build time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
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

    /// Whether thinking/reasoning is enabled.
    #[serde(default)]
    pub thinking_enabled: bool,

    /// Sampling temperature (0.0–2.0).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f64>,

    /// Hard limit on output tokens.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<u32>,

    /// Provider-specific extra parameters.
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,
}
```

逐行：
- `provider` / `model_name` — Phase 3 已有字段，不变
- `endpoint: Option<String>` — 覆盖 Provider 默认 endpoint（新字段）。`None` 用 Provider 内置默认值；`Some(url)` 直接传 ModelAdapter
- `api_key_env: Option<String>` — 环境变量名（新字段）。`None` 用 Provider 默认 env var；`Some("MY_KEY")` 让 ModelAdapter 从该环境变量读
- `thinking_enabled` — 从 ModelSpec 继承，类型不变
- `temperature: Option<f64>` — 类型从 `f32` 改为 `f64`，与 Provider SDK（OpenAI Python SDK 用 float）对齐
- `max_output_tokens: Option<u32>` — 不变
- `extra: Value` — 不变
- 全部 `#[serde(default)]` / `skip_serializing_if`，最简 JSON 只需 `{"provider": "x", "model_name": "y"}`

---

### 切面 2：`crates/arf-agent/src/lib.rs` — 追加 re-export

```rust
//! ARF AgentConfig — declarative resource configuration.
//!
//! Agent declares WHAT it needs: model, tools/skills (MCP), custom nodes.
//! Engine reads AgentConfig and resolves each logical resource to a
//! concrete NodeId on the Bus at build time.

mod config;
mod model;
mod resource;
mod tool;

pub use config::{AgentConfig, ConfigError};
pub use model::ModelDecl;
pub use resource::ResourceSpec;
pub use tool::{ToolPermission, ToolSpec};
```

逐行：
- `mod model` 已有；`ModelSpec` → `ModelDecl`（pub use 改名）
- `ResourceSpec` 不变；仍在 `resource.rs`（内容不变，仅 docstring 更新到 Phase 7 语义）
- `config.rs` 中的旧 `AgentConfig`（subagents/teammates/models/tools）标记 `#[deprecated]` 或删除——Engine 不再使用它，改用 arf-engine 内的新 AgentConfig

---

### 切面 3：`crates/arf-engine/Cargo.toml` — 加 arf-agent 依赖

```toml
[dependencies]
arf-core = { path = "../arf-core" }
arf-bus = { path = "../arf-bus" }
arf-state = { path = "../arf-state" }
arf-agent = { path = "../arf-agent" }   # ← new
```

---

### 切面 4：`crates/arf-engine/src/config.rs` — AgentConfig 重组

完整替换当前文件内容：

```rust
//! AgentConfig — Engine 的全量声明式配置（Phase 7 §2）。

use std::collections::HashMap;
use std::sync::Arc;

use arf_agent::{ModelDecl, ResourceSpec};
use arf_core::{CheckpointRule, ResponseProcessor, Route};

/// Engine 运行期配置——嵌套在 AgentConfig 内。
pub struct EngineConfig {
    /// 仅存自定义 msg_type 的路由（checkpoint 派发的 "summarize" / "send_email" 等）。
    /// model_call / tool_exec 的路由由 Registry 从 resources 推导。
    pub routes: HashMap<String, Route>,

    pub checkpoint_rules: Vec<CheckpointRule>,

    /// 非内置 msg_type 的响应处理。
    pub processors: HashMap<String, Arc<dyn ResponseProcessor>>,

    /// Node 掉线 hook。None = FailSession。
    pub on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>,

    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,
}

/// Node failure handler — invoked by Engine when a member goes offline.
pub trait OnMemberFailedHandler: Send + Sync {
    fn handle(&self, agent: &arf_core::NodeId, member: &arf_core::NodeId, reason: &str)
        -> MemberFailedAction;
}

impl<F> OnMemberFailedHandler for F
where
    F: Fn(&arf_core::NodeId, &arf_core::NodeId, &str) -> MemberFailedAction + Send + Sync,
{
    fn handle(&self, agent: &arf_core::NodeId, member: &arf_core::NodeId, reason: &str) -> MemberFailedAction {
        self(agent, member, reason)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum MemberFailedAction {
    FailSession,
    Retry { delay_ms: u64 },
    SwitchTo { alternative: arf_core::NodeId },
}

impl Default for MemberFailedAction {
    fn default() -> Self { Self::FailSession }
}

/// 完整 Agent 配置——声明 + 运行期。
///
/// **不 derive Clone/Debug**：EngineConfig 含 `Arc<dyn Trait>` / 闭包。
pub struct AgentConfig {
    /// 单模型声明。
    pub model: ModelDecl,

    /// 统一资源声明。
    /// - node_type="mcp"      → Engine 提取 tools/skills 子集
    /// - node_type="mcp/pool" → NodePool（内部 sub-bus）
    /// - 其他 node_type        → 自定义节点，存入路由表
    pub resources: Vec<ResourceSpec>,

    pub system_prompt_template: String,
    pub initial_memory: Vec<String>,
    pub allowed_paths: Vec<String>,

    pub engine: EngineConfig,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            model: ModelDecl {
                provider: "deepseek".into(),
                model_name: "deepseek-v4-flash".into(),
                endpoint: None,
                api_key_env: None,
                thinking_enabled: false,
                temperature: None,
                max_output_tokens: None,
                extra: serde_json::Value::Null,
            },
            resources: vec![],
            system_prompt_template: "You are a helpful assistant.".into(),
            initial_memory: vec![],
            allowed_paths: vec![],
            engine: EngineConfig {
                routes: HashMap::new(),
                checkpoint_rules: vec![],
                processors: HashMap::new(),
                on_member_failed: None,
                max_turns: 10,
                tool_timeout_ms: Some(30_000),
            },
        }
    }
}
```

逐行：
- `use arf_agent::{ModelDecl, ResourceSpec}` — 声明类型来自复活后的 arf-agent
- `EngineConfig` — 从 AgentConfig 中剥离的运行期配置，6 个字段（瘦身：删 tools/skills include/exclude，删 permissions，删 agent_id）
- `AgentConfig` — 声明 + 运行期聚合：`model` + `resources` + 3 个顶层字段 + `engine` 嵌套
- `Default` — model 默认 deepseek-v4-flash；resources 空（无工具/skill 的极简 agent）；system_prompt_template 无 `{{skills}}`
- **删除的类型**：`ModelConfig`、`PermissionConfig`

---

### 切面 5：`crates/arf-engine/src/registry.rs` — 新文件：ResourceRegistry

```rust
//! ResourceRegistry — 声明资源 → NodeId 静态映射（Phase 7 §4）。

use std::collections::HashMap;

use arf_agent::ResourceSpec;
use arf_bus::Bus;
use arf_core::{BusGraph, NodeId, ToolSpec};

use crate::config::AgentConfig;
use crate::error::BuildError;

/// 声明 capabilities 的过滤模式。
enum DeclaredFilter {
    /// "all" sentinel — 显式全取，无 warning。
    All,
    /// 显式白名单。
    Subset(Vec<String>),
    /// capabilities 为 None — 全不取（安全默认）。
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
    /// 工具名 → owner NodeId（build 时从 mcp_nodes 计算，防重名）。
    tool_index: HashMap<String, NodeId>,
    /// Skills 缓存：hash 不变则复用上次文本。
    skills_cache: std::sync::Mutex<(u64, String)>,
}

impl ResourceRegistry {
    /// 从声明 + Bus 拓扑 snapshot 构建。
    pub(crate) fn build(
        decl: &AgentConfig,
        snapshot: &BusGraph,
    ) -> Result<Self, BuildError> {
        // 1. 解析 model
        let model = resolve_model(decl, snapshot)?;

        // 2. 解析 resources
        let mut mcp_nodes = HashMap::new();
        let mut custom_nodes = HashMap::new();
        let mut tool_index = HashMap::new();

        for spec in &decl.resources {
            let node = snapshot.nodes.iter()
                .find(|n| n.node_type == spec.node_type)
                .ok_or_else(|| BuildError::MissingNodes {
                    nodes: vec![format!("{}: node_type=\"{}\"", spec.name, spec.node_type)],
                })?;

            let filter = parse_declared_filter(&spec.capabilities, &spec.name);

            let binding = ResourceBinding {
                resource_name: spec.name.clone(),
                node_id: node.node_id.clone(),
                declared_filter: filter,
            };

            if spec.node_type == "mcp" || spec.node_type.starts_with("mcp/") {
                // 从 MCP 节点的实际 capabilities.tools 读取工具列表，
                // 用 declared_filter 过滤后填充 tool_index（含 "all" 模式）
                let node_actual_tools: Vec<String> = node
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

                for tname in &node_actual_tools {
                    if !filter.accepts(tname) {
                        continue;
                    }
                    if let Some(existing) = tool_index.get(tname) {
                        return Err(BuildError::AmbiguousTool {
                            tool: tname.clone(),
                            providers: vec![
                                existing.to_string(),
                                spec.name.clone(),
                            ],
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
                if name.is_empty() { continue; }
                if !binding.declared_filter.accepts(name) { continue; }
                let description = t.get("description").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let params = t.get("params_schema").cloned().unwrap_or(serde_json::json!({}));
                specs.push(ToolSpec::new(name, description, params));
            }
        }
        specs
    }

    /// 收集声明 skills 的 system prefix 文本（hash-cache）。
    pub(crate) fn skills_text(&self, bus: &Bus) -> String {
        let graph = bus.graph();
        // hash = sorted (node_id, skill_name) pairs as bytes
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
                if sname.is_empty() { continue; }
                if !binding.declared_filter.accepts(sname) { continue; }
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
            let items: Vec<String> = pairs.iter()
                .map(|(nid, sn)| format!("- {sn} (from {nid})"))
                .collect();
            format!("Available skills:\n{}", items.join("\n"))
        };
        *cache = (hash, content.clone());
        content
    }

    /// 查询自定义节点的 NodeId。
    pub(crate) fn owner_of_custom(&self, msg_type: &str) -> Option<NodeId> {
        self.custom_nodes.values()
            .find(|b| b.resource_name == msg_type)
            .map(|b| b.node_id.clone())
    }
}

// ── helpers ──

fn resolve_model(decl: &AgentConfig, snapshot: &BusGraph) -> Result<NodeId, BuildError> {
    let node = snapshot.nodes.iter().find(|n| {
        n.node_type == "model"
            && n.capabilities.get("provider").and_then(|v| v.as_str())
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

fn parse_declared_filter(capabilities: &Option<serde_json::Value>, resource_name: &str) -> DeclaredFilter {
    let caps = match capabilities {
        Some(c) => c,
        None => {
            log::warn!(
                "Resource '{}' declared no capabilities filter — no tools/skills will be registered. \
                 Add explicit capabilities: {{\"tools\": [...], \"skills\": [...]}} or use \"all\" to take everything.",
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
        // 空数组 `{"tools": []}` 不是 "all"——视为未声明
        log::warn!(
            "Resource '{}' declared empty capabilities array — no tools/skills will be registered. \
             Use \"all\" to take everything, or list specific names.",
            resource_name
        );
        DeclaredFilter::None_
    } else {
        DeclaredFilter::Subset(all_names)
    }
}

impl DeclaredFilter {
    fn accepts(&self, _name: &str) -> bool {
        match self {
            DeclaredFilter::All => true,
            DeclaredFilter::Subset(names) => names.iter().any(|n| n == _name),
            DeclaredFilter::None_ => false,
        }
    }
}
```

逐行：
- `ResourceBinding` — 一个资源的解析结果：名字 + NodeId + 过滤模式
- `DeclaredFilter` — 三种语义：`All`（None / "all" / 空数组）、`Subset`（显式白名单）
- `tool_index: HashMap<String, NodeId>` — 工具名 → owner 的 O(1) 查询，替代 `find_tool_owner` 的 O(n) 扫描
- `skills_cache` — 从 `collect_skills_cached` 迁移；key 是 `(node_id, skill_name)` 对的 hash
- `build()` — 遍历 `decl.resources`，每条：找到 node_type 匹配的节点 → 解析 filter → 校验工具名唯一 → 存入对应 map
- `resolve_model()` — 按 `provider` 字符串匹配 `node_type="model"` 节点
- `parse_declared_filter()` — None → warn + None_（全不取）；`"all"` sentinel → All（全取）；白名单 → Subset；空数组 → warn + None_（全不取）
- `tools_for_model()` — 遍历 mcp_nodes，读 Bus 上的 `capabilities.tools`，用 filter 过滤
- `skills_text()` — 同模式，读 `capabilities.skills`，hash-cache
- `owner_of_tool()` — HashMap::get，O(1)

---

### 切面 6：`crates/arf-engine/src/error.rs` — 追加 AmbiguousTool

在 `BuildError` 枚举中追加一个 variant：

```rust
/// 声明了两个 resource 提供同一工具名。
#[error("ambiguous tool '{tool}': declared by both {providers:?}")]
AmbiguousTool {
    tool: String,
    providers: Vec<String>,
},
```

---

### 切面 7：`crates/arf-engine/src/builder.rs` — build() 改用 Registry

```rust
//! EngineBuilder — build-time fail-fast validation（Phase 7 §1）。

use std::collections::HashSet;
use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{NodeId, NodeInfo, Route};

use crate::config::AgentConfig;
use crate::engine::Engine;
use crate::error::BuildError;
use crate::registry::ResourceRegistry;

pub struct EngineBuilder {
    buses: Vec<Arc<Bus>>,
}

impl EngineBuilder {
    pub fn new(buses: Vec<Arc<Bus>>) -> Self {
        Self { buses }
    }

    pub async fn build(self, config: AgentConfig) -> Result<Engine, BuildError> {
        if self.buses.is_empty() {
            return Err(BuildError::MissingNodes {
                nodes: vec!["<no bus provided>".into()],
            });
        }

        // 1. aggregate multi-Bus graph
        let mut merged: HashMap<NodeId, NodeInfo> = HashMap::new();
        for bus in &self.buses {
            let graph = bus.graph();
            for node in graph.nodes {
                merged.entry(node.node_id.clone()).or_insert(node);
            }
        }

        // 2. 解析声明 → ResourceRegistry
        let snapshot = arf_core::BusGraph {
            nodes: merged.values().cloned().collect(),
            message_count: 0,
            uptime_ms: 0,
        };
        let registry = ResourceRegistry::build(&config, &snapshot)?;

        // 3. 校验自定义 msg_type 路由
        for (_msg_type, route) in &config.engine.routes {
            if let Route::Strict(ids) = route {
                let missing: Vec<String> = ids
                    .iter()
                    .filter(|id| !merged.contains_key(id))
                    .map(|id| id.to_string())
                    .collect();
                if !missing.is_empty() {
                    return Err(BuildError::MissingNodes { nodes: missing });
                }
            }
        }

        // 4. CheckpointRule name 唯一
        let mut seen: HashSet<String> = HashSet::new();
        for rule in &config.engine.checkpoint_rules {
            if !seen.insert(rule.name.clone()) {
                return Err(BuildError::DuplicateRuleName {
                    name: rule.name.clone(),
                });
            }
        }

        Engine::new(self.buses, config, registry).await
    }
}
```

逐行：
- 删旧 3 步校验（Strict routes、Discovery routes、NoModelResponder）→ `ResourceRegistry::build` 一次性完成
- 删 `{{skills}}` 替换逻辑；Engine::new 只传 system_prompt_template 原样
- `snapshot` 从 merged 构造，传给 Registry::build
- 保留：multi-Bus 聚合、CheckpointRule name 唯一、自定义 msg_type 的 Strict route 校验

---

### 切面 8：`crates/arf-engine/src/engine.rs` — Engine 使用 Registry

**删**：`collect_skills_cached`（~40 行）、`collect_tools_from_routes`（~30 行）、`find_tool_owner`（~25 行）。
**改**：`Engine` 结构体，`Engine::new`，`do_model_turn`，`do_tool_turn`。

```rust
// Engine struct 变更
pub struct Engine {
    config: AgentConfig,
    agent_id: NodeId,
    handle: NodeHandle,
    primary_bus: Arc<arf_bus::Bus>,
    discovery_cache: Arc<DiscoveryCache>,
    system_prompt_template: String,
    initial_memory: Vec<String>,
    registry: ResourceRegistry,   // ← new；替代 skills_cache
}
```

`Engine::new` 签名增加 registry 参数：

```rust
pub(crate) async fn new(
    buses: Vec<Arc<arf_bus::Bus>>,
    config: AgentConfig,
    registry: ResourceRegistry,   // ← new
) -> Result<Self, BuildError> {
    let primary = buses[0].clone();
    let info = NodeInfo {
        node_id: NodeId::new(format!("engine/{}", config.model.provider)),
        node_type: "engine".into(),
        capabilities: serde_json::json!({
            "kind": "engine",
            "provider": config.model.provider,
            "model": config.model.model_name,
        }),
        online_since: 0,
    };

    let types = engine_response_types(&config);
    let filter = arf_core::MessageFilter {
        types: if types.is_empty() { None } else { Some(types) },
        to_match: ToMatch::BroadcastAndDirectedToMe,
    };

    let handle = primary
        .connect(info.clone(), filter)
        .await
        .map_err(|e| BuildError::PrimaryBusConnect(e.to_string()))?;

    let discovery_cache = Arc::new(DiscoveryCache::new());
    let cache_for_listener = discovery_cache.clone();
    let mut lifecycle_rx = primary.subscribe();
    tokio::spawn(async move {
        while let Ok(m) = lifecycle_rx.recv().await {
            if m.msg_type == "node_online" || m.msg_type == "node_offline" {
                cache_for_listener.invalidate();
            }
        }
    });

    Ok(Self {
        config,
        agent_id: info.node_id,
        handle,
        primary_bus: primary.clone(),
        discovery_cache,
        system_prompt_template: config.system_prompt_template.clone(),
        initial_memory: config.initial_memory.clone(),
        registry,
    })
}
```

`do_model_turn` — skills + tools 从 registry 取：

```rust
async fn do_model_turn(
    &mut self,
    state: &mut State,
    cancel: CancellationToken,
) -> Result<(String, Vec<ToolCall>), RunError> {
    state.over_view.turn_count += 1;

    // 步骤 3：感知注册
    let skills_text = self.registry.skills_text(&self.primary_bus);
    let tools = self.registry.tools_for_model(&self.primary_bus);

    let mut messages: Vec<ModelMessage> = Vec::with_capacity(
        2 + self.initial_memory.len() + 1 + state.messages.len(),
    );
    messages.push(ModelMessage::new("system", &self.system_prompt_template));
    for m in &self.initial_memory {
        messages.push(ModelMessage::new("system", m));
    }
    if !skills_text.is_empty() {
        messages.push(ModelMessage::new("system", &skills_text));
    }
    messages.extend(state.messages.iter().cloned());

    let model_call = ModelCall::new(messages).with_tools(tools);
    let cid = model_call.correlation_id;

    let target = self.registry.model_target();
    let msg = Message::new(
        model_call.msg_type(),
        self.agent_id.clone(),
        vec![target.clone()],
        model_call.payload(),
    );

    // 步骤 4：发送
    let receipt = self.handle.send_msg(msg).await?;
    if receipt.matching_nodes == 0 {
        log::warn!("model_call to {target}: zero matching nodes");
        return Err(RunError::Internal(format!(
            "model node {target} not responding"
        )));
    }

    // wait response...
    // (同现有逻辑)
}
```

`do_tool_turn` — 用 `registry.owner_of_tool()` 替代 `find_tool_owner()`：

```rust
async fn do_tool_turn(
    &mut self,
    state: &mut State,
    tool_call: &ToolCall,
    cancel: CancellationToken,
) -> Result<(), RunError> {
    state.over_view.turn_count += 1;

    let owner = self.registry.owner_of_tool(&tool_call.name)
        .ok_or_else(|| RunError::Internal(format!(
            "tool '{}' not declared in any resource",
            tool_call.name
        )))?;

    let tool_exec = ToolExec {
        correlation_id: Uuid::new_v4(),
        call_id: tool_call.id.clone(),
        name: tool_call.name.clone(),
        arguments: tool_call.arguments.clone(),
        target: Some(owner.clone()),
    };

    let msg = Message::new(
        "tool_exec",
        self.agent_id.clone(),
        vec![owner],
        tool_exec.payload(),
    );

    let receipt = self.handle.send_msg(msg).await?;
    if receipt.matching_nodes == 0 {
        log::warn!("tool_exec '{}' to {owner}: zero matching nodes", tool_call.name);
        // push error tool_result and continue
    }

    // wait response...
    // (同现有逻辑)
}
```

逐行：
- `Engine` 结构体删 `skills_cache: Mutex<(u64, String)>`，加 `registry: ResourceRegistry`
- `Engine::new` — agent_id 从 `config.model.provider` 推导（替代 `config.agent_id`）；capabilities 加 `provider` + `model` 字段（调试用）
- `do_model_turn` — `skills_text` 从 `self.registry.skills_text()` 拿；`tools` 从 `self.registry.tools_for_model()` 拿；`to` 从 `self.registry.model_target()` 拿；`matching_nodes == 0` 即时 fail
- `do_tool_turn` — `owner` 从 `self.registry.owner_of_tool()` 拿（O(1) vs 旧 `find_tool_owner` 的 O(n)）；未声明的工具 `RunError::Internal`；`matching_nodes == 0` 即时 fail

---

### 切面 9：`crates/arf-engine/src/lib.rs` — 更新 export

```rust
pub mod builder;
pub mod checkpoint;
pub mod config;
pub mod engine;
pub mod error;
pub(crate) mod registry;  // ← new
#[cfg(test)]
mod tests;

pub use arf_core::WaitStrategy;
pub use builder::EngineBuilder;
pub use config::{
    AgentConfig, EngineConfig, MemberFailedAction, OnMemberFailedHandler,
};
pub use engine::Engine;
pub use error::{BuildError, RunError};
```

删 `ModelConfig`、`PermissionConfig` 的 re-export；加 `EngineConfig`。

---

### 切面 10：`py-arf/src/engine.rs` — Python 绑定同步

PyAgentConfig 的 `#[new]` 签名改为：

```rust
#[new]
#[pyo3(signature = (
    provider = "deepseek".to_string(),
    model = "deepseek-v4-flash".to_string(),
    endpoint = None,
    api_key_env = None,
    system_prompt_template = "You are a helpful assistant.".to_string(),
    resources = None,
    max_turns = 10u32,
    tool_timeout_ms = None,
    routes = None,
    checkpoint_rules = None,
))]
fn new(
    provider: String,
    model: String,
    endpoint: Option<String>,
    api_key_env: Option<String>,
    system_prompt_template: String,
    resources: Option<Vec<PyResourceSpec>>,
    max_turns: u32,
    tool_timeout_ms: Option<u64>,
    routes: Option<std::collections::HashMap<String, PyRoute>>,
    checkpoint_rules: Option<&pyo3::Bound<'_, pyo3::PyAny>>,
) -> PyResult<Self> {
    // ...
    let cfg = AgentConfig {
        model: ModelDecl {
            provider,
            model_name: model,
            endpoint,
            api_key_env,
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: None,
            extra: Value::Null,
        },
        resources: resources.unwrap_or_default().into_iter().map(|r| r.inner).collect(),
        system_prompt_template,
        initial_memory: vec![],
        allowed_paths: vec![],
        engine: EngineConfig {
            routes: /* ... */,
            checkpoint_rules: rules,
            processors: HashMap::new(),
            on_member_failed: None,
            max_turns,
            tool_timeout_ms,
        },
    };
    // ...
}
```

新增 `PyResourceSpec` wrapper（轻量，透传 `ResourceSpec` 字段）：

```rust
#[pyclass(name = "ResourceSpec")]
#[derive(Clone)]
pub struct PyResourceSpec {
    pub(crate) inner: ResourceSpec,
}

#[pymethods]
impl PyResourceSpec {
    #[new]
    #[pyo3(signature = (name, node_type, capabilities = None))]
    fn new(
        name: String,
        node_type: String,
        capabilities: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let caps_json = match capabilities {
            Some(obj) => Some(py_object_to_json(&obj, Python::acquire_gil().python())?),
            None => None,
        };
        Ok(Self {
            inner: ResourceSpec { name, node_type, capabilities: caps_json },
        })
    }
}
```

删 `agent_id` 参数（Engine 从 ModelDecl 推导）。

---

### 切面 11：`py-arf/src/lib.rs` — 加 PyResourceSpec export

```rust
pub use engine::{PyAgentConfig, PyEngine, PyEngineBuilder, PyResourceSpec, PyState};
```

---

## 测试

### Registry 单测（`crates/arf-engine/src/registry.rs` 内 `#[cfg(test)]`）

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use arf_agent::ResourceSpec;
    use arf_core::NodeInfo;

    fn test_snapshot(nodes: Vec<NodeInfo>) -> BusGraph {
        BusGraph { nodes, message_count: 0, uptime_ms: 0 }
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

    // ═══════════════════════════════════════════════════
    // 构造
    // ═══════════════════════════════════════════════════

    // [构造] 合法声明 → Registry 构建成功
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
            mcp_node("mcp/files", serde_json::json!([
                {"name": "read", "description": "read file", "params_schema": {}}
            ])),
        ]);
        let registry = ResourceRegistry::build(&decl, &snapshot).unwrap();
        assert_eq!(registry.model_target().as_str(), "model/deepseek");
        assert_eq!(registry.owner_of_tool("read").unwrap().as_str(), "mcp/files");
    }

    // ═══════════════════════════════════════════════════
    // 错误
    // ═══════════════════════════════════════════════════

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
            mcp_node("mcp/files", serde_json::json!([{"name": "read"}])),
            mcp_node("mcp/code", serde_json::json!([{"name": "read"}])),
        ]);
        assert!(matches!(
            ResourceRegistry::build(&decl, &snapshot),
            Err(BuildError::AmbiguousTool { .. })
        ));
    }

    // ═══════════════════════════════════════════════════
    // 过滤
    // ═══════════════════════════════════════════════════

    // [过滤] 声明子集 ∩ 全量 → 只返回声明的工具
    #[test]
    fn registry_tools_for_model_filters_by_declaration() {
        let bus = Arc::new(/* test bus with mcp node */);
        // ... 连接 mcp node with tools: [read, write, bash]
        // 声明 capabilities: {"tools": ["read", "bash"]}
        // assert tools_for_model 返回 [read, bash]，不含 write
    }

    // [查询] owner_of_tool → 正确的 NodeId
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
        assert_eq!(registry.owner_of_tool("read").unwrap().as_str(), "mcp/files");
        assert!(registry.owner_of_tool("nonexistent").is_none());
    }

    // [安全] capabilities=None → build 成功 + log 含 warning + 不注册任何工具
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
        // None → 不注册任何工具
        assert!(registry.owner_of_tool("read").is_none());
    }

    // [显式] capabilities={"tools":"all"} → 全取
    #[test]
    fn registry_build_all_sentinel_accepts_all() {
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
            mcp_node("mcp/files", serde_json::json!([{"name": "read"}, {"name": "write"}])),
        ]);
        let registry = ResourceRegistry::build(&decl, &snapshot).unwrap();
        assert!(registry.owner_of_tool("read").is_some());
        assert!(registry.owner_of_tool("write").is_some());
    }
}
```

### 现有测试适配

- 所有 `AgentConfig` 构造改为新字段：`model: ModelDecl { ... }` + `resources: vec![...]` + `engine: EngineConfig { ... }`
- `find_tool_owner` 相关测试 → 替换为 `registry.owner_of_tool` 测试
- `collect_tools_from_routes` 相关测试 → 替换为 `registry.tools_for_model` 测试
- `collect_skills_cached` 相关测试 → 替换为 `registry.skills_text` 测试
- `ModelConfig` 引用 → `ModelDecl`
- `cargo test --workspace` 全绿
