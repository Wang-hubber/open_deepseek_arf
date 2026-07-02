# Resource Registry — 统一资源声明与路由

**Date**: 2026-07-02
**Status**: Draft
**Scope**: `arf-agent` 复活 → `arf-engine` AgentConfig 重组 → ResourceRegistry 替代 3 个散落函数 → Python 绑定同步

## 动机

编制 `docs/api/tutorials/tools.md` 示例时发现 Engine ↔ AgentConfig ↔ MCP ↔ ModelAdapter 四者的职责边界模糊。AgentConfig 已膨胀到 14 个字段，Engine 内 `collect_tools_from_routes` / `collect_skills_cached` / `find_tool_owner` 三个函数互不相关。同时 `arf-agent` crate（Phase 3 的声明层设计）从未被 Engine 实际采用。

**收敛方向**：五步声明-校验-注册-调用-执行闭环，每步职责单一。

## 已有决策

| # | 决策 | 选择 |
|---|------|------|
| 1 | 改动范围 | Engine 实现 + AgentConfig + 3 个 collect 函数全部重构 |
| 2 | 声明层 | 复活 `arf-agent` crate，Engine 依赖它 |
| 3 | API 形态 | `AgentConfig { ..declaration, engine: EngineConfig }` 单配置嵌套 |
| 4 | Skills 发现 | 显式声明（`ResourceSpec.capabilities`），废弃自动 `kind=skill` 收集 |
| 5 | 投递模型 | broadcast + `to: Vec<NodeId>` + filter；禁用 p2p 直发 |
| 6 | 在线状态 | Bus 是单一可信源，Engine 不维护在线集合 |
| 7 | 离线处理 | `SendError::NodeOffline` 或 `matching_nodes=0` → 即时 fail，不进入 wait |
| 8 | capabilities 缺省 | `None` → build warning + **全不取**；`"all"` sentinel → 显式全取 |
| 9 | 命名冲突 | 声明重名工具 → `BuildError::AmbiguousTool` |
| 10 | 并发 | 同一资源只有一个 NodeId；NodePool 内部 sub-bus 管理多 worker |
| 11 | 资源分类 | `node_type` 字符串区分 model / mcp / 自定义，不引入 enum variant |

---

## §1 职责边界

**核心约束**：Engine 代码不 `use` 任何具体 Node crate；广播 + filter 保证 trace 完整性。

| 角色 | 做什么 | **不**做什么 | 持有状态 |
|------|--------|------------|------|
| **AgentConfig** | 声明 `model: ModelDecl` + `resources: Vec<ResourceSpec>` — 逻辑名清单 | 不解析、不执行、不引用 NodeId | 纯数据 |
| **EngineBuilder.build** | (a) 调 `bus.snapshot()` 解析所有声明 → NodeId (b) 校验声明节点在线 → fail-fast (c) 校验无重名工具 | 不订阅事件、不维护在线状态 | — |
| **ResourceRegistry** | `target_of(kind, name) → Option<NodeId>` · `tools_for_model() → Vec<ToolSpec>` · `skills_text() → String` | 不知在线状态、不订阅 Bus 事件 | `HashMap<ResourceKey, NodeId>`(build 后 immutable) |
| **Engine** | (a) 调 registry 拿 `to` 节点 (b) 调 `bus.publish(msg.with_to(vec![to]))` (c) `SendError::NodeOffline` / `matching_nodes=0` → 即时 fail | 不维护 `online: HashSet` · 不订阅 `node_online/offline` · 不用定向 API | `ResourceRegistry` + `Arc<Bus>` |
| **Bus** | heartbeat · `nodes: HashMap` · publish 时校验 directed targets · 广播 `node_online/offline` | 不知资源声明 | `HashMap<NodeId, NodeEntry>` |
| **Node** | 业务实现 + `MessageFilter` 决定接收 | 不知 Engine 状态 | 业务 |

**两个发布模式**：
- **声明资源调用**：`msg.to = vec![target]`（directed）。Bus 在 publish 时校验：全部离线 → `SendError::NodeOffline`；部分离线 → 只投在线。
- **广播调用**（checkpoint 派发的自定义 action）：`msg.to = vec![]`。`receipt.matching_nodes == 0` → warn。

> 详见记忆：[Broadcast over point-to-point]

---

## §2 AgentConfig 重组

```rust
pub struct AgentConfig {
    /// 单模型声明。主流场景同一 agent 用一个模型。
    pub model: ModelDecl,

    /// 统一资源声明。
    /// - node_type="mcp"    → Engine 提取 tools/skills 子集，注册到 model_call + system prefix
    /// - node_type="mcp/pool" → Engine 解析为 NodePool（内部 sub-bus 管理 N 个 worker）
    /// - 其他 node_type      → Engine 存入路由表，供 checkpoint 自定义 action 使用
    pub resources: Vec<ResourceSpec>,

    pub system_prompt_template: String,
    pub initial_memory: Vec<String>,
    pub allowed_paths: Vec<String>,

    pub engine: EngineConfig,
}

pub struct ModelDecl {
    pub provider: String,
    pub model_name: String,
    pub endpoint: Option<String>,       // 覆盖 Provider 默认 endpoint
    pub api_key_env: Option<String>,    // 环境变量名，如 "DEEPSEEK_API_KEY"
    pub thinking_enabled: bool,
    pub temperature: Option<f64>,
    pub max_output_tokens: Option<u32>,
    pub extra: serde_json::Value,
}

pub struct EngineConfig {
    /// 仅存自定义 msg_type 的路由（checkpoint 派发的 "summarize" / "send_email" 等）。
    /// model_call / tool_exec 的路由由 Registry 从 resources 推导。
    pub routes: HashMap<String, Route>,

    pub checkpoint_rules: Vec<CheckpointRule>,
    pub processors: HashMap<String, Arc<dyn ResponseProcessor>>,
    pub on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>,
    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,
}
```

**删除的字段**：
- `agent_id` → Engine 从 `ModelDecl.provider + model_name` + session_id 自行分配
- `tools_include / tools_exclude` → 被 `ResourceSpec.capabilities.tools` 替换
- `skills_include / skills_exclude` → 被 `ResourceSpec.capabilities.skills` 替换
- `model_config: ModelConfig { provider, model }` → 扩展为 `model: ModelDecl`
- `permissions: PermissionConfig` → `allowed_paths` 升到 AgentConfig 顶层

### ResourceSpec：三类资源统一的声明载体

```rust
/// 来自 arf-agent（Phase 3），语义不变。
pub struct ResourceSpec {
    pub name: String,           // Agent 给的别名，如 "file_tools"
    pub node_type: String,      // "mcp" | "mcp/pool" | "custom/email" | ...
    pub capabilities: Option<serde_json::Value>,
    //   None          → build warning："未声明 capabilities filter，该资源不注册任何工具/技能"
    //   Some({"tools": ["read", "bash"]})         → 只注册这些工具
    //   Some({"tools": "all", "skills": "all"})   → 显式全取，无 warning
    //   Some({"skills": ["code_review"]})          → 只注册这个技能
}
```

### 过滤语义

声明 `capabilities` 是做**交集**——MCP 节点全量能力 ∩ 声明子集 = Engine 注入的部分。

```
McpNode capabilities (全量)         声明子集                    Engine 注入
┌─────────────────────────┐     ┌───────────────────┐     ┌──────────────┐
│ tools: [read, write,    │     │ tools:            │     │ read_file    │
│         bash, search]   │     │   [read_file]     │     │ bash         │
│ skills: [review,        │     │   [bash]          │     └──────────────┘
│          compress]      │     │ skills:           │     ┌──────────────┐
└─────────────────────────┘     │   [review]        │     │ review       │
                                └───────────────────┘     └──────────────┘
```

**不在声明子集里的能力** → Engine 不注入、LLM 不可见。

---

## §3 数据流（5 步全链路）

### 步骤 1 — 声明

```rust
AgentConfig {
    model: ModelDecl {
        provider: "deepseek".into(),
        model_name: "deepseek-v4-flash".into(),
        endpoint: None,
        api_key_env: Some("DEEPSEEK_API_KEY".into()),
        thinking_enabled: false,
        temperature: Some(0.7),
        max_output_tokens: Some(4096),
        extra: Value::Null,
    },
    resources: vec![
        ResourceSpec {
            name: "file_tools".into(),
            node_type: "mcp".into(),
            capabilities: Some(json!({"tools": ["read_file", "bash"]})),
        },
    ],
    system_prompt_template: "You are a helpful assistant.".into(),
    initial_memory: vec![],
    allowed_paths: vec!["/workspace".into()],
    engine: EngineConfig {
        routes: HashMap::new(),
        checkpoint_rules: vec![],
        processors: HashMap::new(),
        on_member_failed: None,
        max_turns: 10,
        tool_timeout_ms: Some(30_000),
    },
}
```

### 步骤 2 — 校验（EngineBuilder::build）

```
1. bus.snapshot() → BusGraph { nodes: [NodeInfo*] }
2. 解析 model: 在 nodes 中找 node_type="model" + capabilities.provider 匹配 → NodeId
3. 解析 resources:
   for each spec in resources:
     a. 按 node_type 匹配 nodes → NodeId
     b. 校验 NodeId 在线 → BuildError::MissingNode
     c. 按 spec.capabilities 过滤，提取工具名 → 存入 binding
4. 工具名唯一性检查:
   同一 tool_name 出现在多个 resource 的声明的 capabilities.tools 中
   → BuildError::AmbiguousTool { tool: "read", providers: ["files", "code"] }
5. Registry::build(decl, snapshot) → 冻结 ResourceRegistry
6. capabilities 为 None → log::warn!("资源 '{}' 未声明 capabilities filter——不注册任何工具/技能", name)
```

### 步骤 3 — 感知注册（do_model_turn）

```
Engine.prepare_model_turn():
  messages = [
    system(system_prompt_template),      // 最稳定
    system(initial_memory[0]),           // session 稳定
    system(registry.skills_text(bus)),  // hash-cache，低频变化
    ...state.messages,                   // 每轮增长
  ]
  tools = registry.tools_for_model(bus)  // 从声明子集提取
  model_call = ModelCall::new(messages).with_tools(tools)
              .with_to(vec![registry.model_target()])
  bus.publish(model_call)
```

### 步骤 4 — 调用（turn 驱动）

**model_call:**
```
target = registry.model_target()
msg = ModelCall { ..., to: vec![target] }
match bus.publish(msg).await:
    Ok(receipt) if receipt.matching_nodes > 0 →
        wait_for_response(correlation_id, timeout)
    Ok(receipt) if receipt.matching_nodes == 0 →
        log::warn!("broadcast to zero matching nodes for model_call")
        return ChatResult.failed("no model responder")
    Err(SendError::NodeOffline(ids)) →
        log::warn!("model node offline: {ids:?}")
        return ChatResult.failed(format!("model node {ids:?} offline"))
```

**tool_exec:**
```
owner = registry.owner_of_tool(tc.function.name)  // HashMap 查询，O(1)
msg = ToolExec { ..., to: vec![owner] }
match bus.publish(msg).await:
    // 同 model_call 的三路分支
```

### 步骤 5 — 执行

```
ModelAdapterNode  → model_response  → Engine.consume_model_response()
McpNode           → tool_result     → Engine.consume_tool_result()
User-defined Node → custom response → Engine.processors[msg_type].handle()
```

---

## §4 ResourceRegistry 实现

替代 `collect_tools_from_routes` + `collect_skills_cached` + `find_tool_owner`。

```rust
struct ResourceBinding {
    resource_name: String,
    node_id: NodeId,
    declared_filter: DeclaredFilter,
}

enum DeclaredFilter {
    All,                        // "all" sentinel — 显式全取
    Subset(Vec<String>),       // 显式白名单
    None_,                      // capabilities 缺省 — 全不取
}

struct ResourceRegistry {
    model: ResourceBinding,
    mcp_nodes: HashMap<NodeId, ResourceBinding>,
    custom_nodes: HashMap<NodeId, ResourceBinding>,  // 非 model/mcp
    // 工具名 → owner NodeId（build 时从 mcp_nodes 的 declared_filter 计算）
    tool_index: HashMap<String, NodeId>,
}
```

**构建**：`Registry::build(decl: &AgentConfig, snapshot: &BusGraph) -> Result<Self>`

**查询接口**：
| 方法 | 返回值 | 调用方 |
|------|--------|--------|
| `model_target() → NodeId` | model 节点 | Engine.step_4 |
| `skills_text(bus) → String` | system prefix 片段 | Engine.step_3 |
| `tools_for_model(bus) → Vec<ToolSpec>` | LLM function-calling 格式 | Engine.step_3 |
| `owner_of_tool(name) → Option<NodeId>` | 拥有该工具的 MCP 节点 | Engine.step_4 |
| `owner_of_custom(msg_type) → Option<NodeId>` | 自定义节点（查 custom_nodes） | Engine checkpoint 派发 |

**skills 缓存**：从 `collect_skills_cached` 迁移——`skills_cache: Mutex<(u64, String)>`。hash 由 `(node_id, capabilities.skills 内容)` 计算。node_online/offline 不触发 invalidate（build 时冻结的拓扑不变）。

---

## §5 错误模型

```
阶段              错误类型                          处理方            效果
──────────────────────────────────────────────────────────────────────────
步骤 2 (build)    BuildError::MissingNode          EngineBuilder     拒绝启动，fail-fast
                  BuildError::AmbiguousTool        EngineBuilder     拒绝启动，提示冲突方
                  BuildError::NoModelResponder     EngineBuilder     拒绝启动
步骤 4 (send)     SendError::NodeOffline(ids)      Bus → Engine      Engine 即时 fail，不进入 wait
                  matching_nodes == 0              Engine            即时 fail，不进入 wait
                  matching_nodes > 0               Engine            正常 wait (≤ tool_timeout_ms)
步骤间 (wait)     WaitTimeout                      Engine            warn + ChatResult.failed
步骤 5 (execute)  Response::Done(value)            Node → Engine     正常；灌入 state.messages
                  Node panic / 内部错误             Node → Bus 掉线   Bus heartbeat 检测 → node_offline
```

**三层发送防护**（Engine 不 hang）：

```
bus.publish(msg) 结果            Engine 行为                    最长等待
─────────────────────────────────────────────────────────────────────────
Ok, matching > 0               wait_for_response(cid, timeout)  tool_timeout_ms
Ok, matching == 0              即时 fail                       0ms
Err(NodeOffline)               即时 fail                       0ms
```

**不设计重试**：model_call 网络错误由 ModelAdapter 的 Provider SDK 自处理。Engine 层不重试。

---

## §6 routes 字段的最终职责

`EngineConfig.routes: HashMap<String, Route>` 仅存**开发者自定义 msg_type** 的路由。

- `model_call` / `tool_exec*` 的路由 → 由 Registry 从 `resources` 推导，不经过 `routes`
- checkpoint 派发的 `"summarize"` / `"send_email"` 等 → 走 `routes`
- 自定义 action 的 `to` 字段 → 从 `routes` 查；查不到则 `to=[]`（广播），靠 `matching_nodes` 判断有无响应方

**与自审查 F3（Route / CheckpointRule.route 优先级）的关系**：`routes` 和 `resources` 查到的 `to` 都是 `Vec<NodeId>`——Engine 只做一次查询，不存在优先级冲突。checkpoint rule 产出的自定义 action 查 `routes`，ReAct 循环产出的 model_call/tool_exec 查 Registry。两条路径互不交叉。

---

## §7 文件改动

| 文件 | 变更 |
|------|------|
| `crates/arf-agent/src/lib.rs` | + `pub use` ModelDecl, ResourceSpec re-export（已有） |
| `crates/arf-agent/src/resource.rs` | ResourceSpec 不变；追加 `DeclaredFilter` / `ModelDecl` 或放独立文件 |
| `crates/arf-agent/src/model.rs` | ModelSpec → ModelDecl 扩展（加 endpoint, api_key_env） |
| `crates/arf-engine/Cargo.toml` | 加 `arf-agent = { path = "../arf-agent" }` 依赖 |
| `crates/arf-engine/src/config.rs` | AgentConfig 重组；EngineConfig 瘦身；删 tools/skills include/exclude；删 PermissionConfig；删 agent_id |
| `crates/arf-engine/src/builder.rs` | build() 调用 `Registry::build`；删旧 3 步校验 → 统一为 registry 校验 |
| `crates/arf-engine/src/engine.rs` | 删 `collect_tools_from_routes` / `collect_skills_cached` / `find_tool_owner`；引入 `ResourceRegistry`；model_call / tool_exec 的 `to` 从 registry 查 |
| `crates/arf-engine/src/registry.rs` | **新文件** — ResourceRegistry + ResourceBinding + DeclaredFilter + build() + 查询方法 |
| `crates/arf-engine/src/tests.rs` | 更新现有测试（AgentConfig 构造、to 字段）；新增 AmbiguousTool / NodeOffline / None-capabilities-warning 测试 |
| `py-arf/src/engine.rs` | Python 绑定同步字段变更 |
| `py-arf/src/lib.rs` | 同步 export |
| `docs/api/tutorials/hello.md` | AgentConfig 示例更新 |
| `docs/api/tutorials/conversation.md` | AgentConfig 示例更新 |
| `docs/api/tutorials/tools.md` | AgentConfig 示例更新（核心受影响文档） |
| `docs/api/explanation/上下文拼装机制.md` | skills 注入方式从自动发现改为声明驱动 |

**不新建 crate**。`arf-agent` 复活后无其他新增。

---

## §8 测试策略

### 新增单测

| 测试 | 覆盖角度 |
|------|----------|
| `registry_build_all_resources_resolved` | [构造] 合法声明 → 解析表正确 |
| `registry_build_missing_model_node` | [错误] node_type="model" 不在线 → MissingNode |
| `registry_build_missing_mcp_node` | [错误] ResourceSpec 声明的 mcp 不在线 → MissingNode |
| `registry_build_ambiguous_tool` | [冲突] 两个 resource 声明同一 tool → AmbiguousTool |
| `registry_build_none_capabilities_rejects_all` | [安全] capabilities=None → build 成功 + log 含 warning + 不注册任何工具 |
| `registry_build_all_sentinel` | [显式] capabilities={"tools":"all"} → 全取 + 无 warning |
| `registry_tools_for_model_filters_by_declaration` | [过滤] 声明子集 ∩ 全量 → 只返回声明的工具 |
| `registry_owner_of_tool_returns_correct_node` | [查询] 工具名 → 正确的 NodeId |
| `engine_send_model_call_node_offline_fails_immediate` | [错误] NodeOffline → 即时 fail，无 wait |
| `engine_send_model_call_zero_matching_fails_immediate` | [错误] matching=0 → 即时 fail |

### 现有测试适配

- 所有 AgentConfig 构造改为新字段（`model: ModelDecl`, `resources: Vec<ResourceSpec>`, `engine: EngineConfig`）
- 删除 `find_tool_owner` 相关测试 → 替换为 `owner_of_tool` 测试
- `collect_tools_from_routes` 相关测试 → 替换为 `tools_for_model` 测试
- `cargo test --workspace` 必须全绿

### 冒烟测试

```bash
.venv/bin/python /tmp/ch1.py  # model-only，不变
.venv/bin/python /tmp/ch2.py  # ReAct loop，tool_call 仍走通
.venv/bin/python /tmp/ch3.py  # 多 MCP，工具路由正确
```

---

## §9 与自审查的修复映射

| 自审查项 | 本设计 |
|----------|--------|
| F2 / A1 / A7 — 配置入口统一 | AgentConfig 单入口；EngineConfig 嵌套；routes 仅自定义；processors / on_member_failed 进 EngineConfig |
| F3 — Route / CheckpointRule.route 优先级 | routes 仅用于自定义 action；声明资源走 Registry，两条路径无交集 |
| C4 / G4 / G13 — 工具注入路径 | Registry.tools_for_model() 替代自动收集；声明 → 解析 → 注入闭环 |
| A8 — 内置 msg_type 白名单 | model_call / tool_exec 由 Registry 推导，不入 routes |
| F1 — 字段名统一 | `model_config` → `model: ModelDecl` |
| A3 — 术语统一 | "park" → "enter waiting state"；"waiting" 是状态，"park" 是动词 |

---

## §10 不在本次范围

- **Heartbeat 协议详细设计**（G9）— Bus 层面独立任务
- **Subagent 组合模型**（G1）— `resources` 中的 `node_type="agent/subagent"` 声明入口已预留，但 Engine 侧的 subagent call 协议另开 spec
- **Session.resume API**（G2）— 独立任务
- **并发模型**（G10）— Engine 仍单 tokio task
- **取消传播**（G6）— 独立任务
- **Multi-bus union** — 单 primary_bus 足够
- **Per-skill turn 更新** — skills 全量替换，非增量

---

## 自审

- [x] **占位符**：无 TBD / TODO
- [x] **内部一致性**：§1 表与 §3 数据流一致；§2 字段与 §4 Registry 接口一致；§5 错误模型与 §3 步骤 4 分支一致
- [x] **范围**：~7 文件 Rust 改动 + 4 文件文档改动 + Python 绑定同步；单 PR / commit 链
- [x] **歧义**：`"all"` sentinel · `None` warning · NodeOffline 即时 fail · AmbiguousTool 构建时报错 · `to` 字段语义 · broadcast+filter 发布模型 均明确陈述
