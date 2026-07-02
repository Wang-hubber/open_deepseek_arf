# ARF 框架实体目录与正交交互表

> **Date**: 2026-07-02
> **Status**: Living document — 跟随 v1.x 各 phase 演进
> **Scope**: 完整列出 v1.x 框架所有顶层实体，并给出每对实体之间**全部可能**的交互模式
>
> **读者**：框架开发者（理解边界）、App 开发者（理解协议）、新人 onboarding

---

## §0 概述

### 0.1 设计目标

v1.x 重构后，框架围绕 **Bus + Node** 模型组织：所有顶层实体都是 Bus 上的参与者（或其抽象）。本文档：

1. **§1**：列出 28 个实体（含 5 个次级子实体），每个一段说明职责 / 关键字段 / 生命周期 / 典型交互
2. **§2**：给出 **23×23 全量正交表**（核心 23 个），按 5 组拆为 15 张子表，覆盖所有 529 个实体对；其余次级实体在 §1 中以子实体卡片补充
3. **§3**：拓扑符号字典 + 关键交互模式总结
4. **§4**：维护说明（如何跟随 phase 演进）+ 源码核对结论

### 0.2 实体分组（28 个实体 / 5 组）

| 组 | 数量 | 实体 | 角色 |
|----|------|------|------|
| **A. 核心机制** | 9 | `Bus` · `Engine` · `MCP` · `ModelAdapter` · `State` · `AgentConfig` · `EngineConfig` · `OnMemberFailedHandler` · `MemberFailedAction` | 框架基础组件 + 嵌套配置 |
| **B. Phase 6 协议层抽象** | 5 | `Node` · `ActionMessage` · `Route` · `CheckpointRule` · `Checkpoint` | 协议层 trait / enum |
| **C. Phase 7 资源装配层** | 7 | `ResourceSpec` · `ToolPermission` · `ToolSpec` · `EngineBuilder` · `ResourceRegistry` · `DiscoveryCache` · `NodeHandle` | 声明层 DI 与缓存 |
| **D. 原子资源类型** | 4 | `Model` · `Tool` · `Skill` · `Hook` | 资源四原语 |
| **E. 用户侧抽象层** | 3 | `Session` · `Round` · `Turn` | 用户视角的时间分层 |

> **说明**：
> - **§2 矩阵覆盖 23 个核心实体**（A=6 · B=5 · C=5 · D=4 · E=3）
> - **§1 卡片覆盖全部 28 个**（额外 5 个次级：EngineConfig / OnMemberFailedHandler / MemberFailedAction / ToolPermission / ToolSpec）
> - **次级实体关系**：EngineConfig ⊂ AgentConfig.engine；ToolPermission ⊂ arf_agent::ToolSpec；ToolSpec 双类型（arf-core 与 arf-agent）

### 0.3 读图约定

- **行 = 发送方 / 操作主体**，**列 = 接收方 / 操作客体**（含自交互 `Entity:Entity`）
- 每个单元格列出**所有可能**的交互模式，格式：`{拓扑符号} {交互名}（{具体行为}）`
- 单元格的"空"（即两个实体无任何交互）也明确标注，避免遗漏
- 自交互行（对角线）展开最详细，含"该实体自身具备的全部能力"

### 0.4 与现有文档的关系

| 文档 | 内容 | 与本文档关系 |
|------|------|------------|
| `docs/dev/v1.x-design.md` | v1.x 总体设计意图 | 本文档的"实做视角"补充 |
| `docs/dev/phase1..7/*` | 每个 phase 的 task 设计 | 本文档的"实体视角"总览 |
| `docs/api/` | 用户 API 参考 | 本文档的简化版（仅 A 组 + D 组 + E 组） |

---

## §1 实体描述卡片

### A 组：核心机制（6 实体）

#### A1. `Bus` — 消息总线

- **职责**：J-RPC 广播消息总线；维护在线节点图（`BusGraph`）；处理节点生命周期（`node_online` / `node_offline` / `heartbeat`）；pub/sub 路由
- **关键字段**：`nodes: HashMap<NodeId, NodeEntry>`、`graph: BusGraph`、广播通道 `tx: broadcast::Sender<Message>`
- **生命周期**：App 启动时 `Bus::new()` 创建；运行时由 Engine / Node / MCP / ModelAdapter 调 `bus.connect(node_info)` 注册；Engine.shutdown 时 Bus 自然 drop
- **典型交互**：`Bus → Engine`（push msg） · `Bus → MCP`（push msg） · `Node → Bus`（connect / publish）

#### A2. `Engine` — ReAct 循环 actor

- **职责**：Bus 上的一个 Node；维护 `AgentConfig + State`；驱动 5 状态机（idle/processing/waiting/stopped）；按订阅式 CheckpointRule 触发消息派发
- **关键字段**：`config: AgentConfig` · `agent_id: NodeId` · `handle: NodeHandle` · `discovery_cache: Arc<DiscoveryCache>` · `registry: ResourceRegistry`
- **生命周期**：`EngineBuilder::build()` 创建；`engine.run(&mut state)` 驱动；App 决定 run 何时结束（stop 信号）
- **典型交互**：`Engine → Bus`（publish model_call / tool_exec / checkpoint msg） · `Engine → State`（push_message / inc_round） · `Engine → ResourceRegistry`（target_of 查表）

#### A3. `MCP` — 资源管理与执行

- **位置**：`crates/arf-mcp/src/node.rs`（**单一类型** `McpNode`，构造差异通过 trait 注入）
- **职责**：Tool / Skill 节点；通过 namespace 隔离；本地 (`FsDiscovery` + `LocalRuntime`) 扫描 `tool.toml` + `SKILL.md`，远程 (`HttpDiscovery` + `RemoteRuntime`) HTTP 代理；DAG 执行 + 级联取消
- **关键字段**：`namespace: String` · `node_id: NodeId`（格式 `mcp/{namespace}`）· `discovery: Box<dyn DiscoveryBackend>` · `runtime: Box<dyn RuntimeModule>` · `handle: Mutex<Option<NodeHandle>>`
- **构造方式**（均返回 `Arc<Self>`）：
  - `McpNode::local(ns, root)` — 文件系统扫描 + 本地 runtime
  - `McpNode::remote(ns, config)` — HTTP discovery + remote runtime
  - `McpNode::local_with_runtime(ns, root, runtime)` — 自定义 RuntimeModule（Docker / Firecracker uVM 等）
- **pool 变体**：`MCPPoolNode`（`crates/arf-mcp/src/pool_node.rs`）用于多 worker 并发
- **生命周期**：`McpNode::connect(&bus)` 注册；connect 时 `MessageFilter { types: None, to_match: ToMatch::All }` 接收所有 msg；Engine 解析 `ResourceSpec` 时按 node_type="mcp" 关联
- **典型交互**：`MCP → Bus`（publish `tool_result` / `tool_result_set`） · `Engine → MCP`（subscribe `tool_exec` / `tool_call_set`） · `MCP → Tool`（通过 RuntimeModule 执行，参见 §A3.1） · `MCP → Skill`（通过 DiscoveryBackend trait 加载，参见 §A3.2）

#### A3.1 `Tool` trait — MCP 内部 Tool 抽象

- **位置**：`crates/arf-mcp/src/tool.rs`
- **关键方法**：`name()` · `description()` · `parameters_schema()` · `async fn execute(params) -> Result<Value, ToolError>` · `async fn cancel()`（默认 no-op）
- **panic 安全**：executor 用 `catch_unwind` 包裹 execute，tool 作者可 `unwrap()` / `expect()` 不破坏 MCP
- **典型交互**：`MCP → Tool`（runtime.execute 调用） · `App → Tool`（实现 trait 注册到 MCP）

#### A3.2 `DiscoveryBackend` trait — MCP 后端抽象

- **位置**：`crates/arf-mcp/src/discovery.rs`
- **关键方法（Tool 部分）**：`list_tools()` · `resolve_tool(name)` · `load_tool_config(skill, tool)`
- **关键方法（Skill 部分）**：`resolve_skill(name)` · `list_skills()` · `load_skill_body(name)` · `load_skill_resources(name)` · `run_skill_tool(...)`
- **Skill 方法默认实现**：`HttpDiscovery` 默认返回 `None` / `Err("skills not supported")`（远程 MCP 不支持 Skill）；`FsDiscovery` 委托 `SkillIndex`
- **典型交互**：`MCP → DiscoveryBackend`（加载 tools/skills） · `FsDiscovery / HttpDiscovery → DiscoveryBackend`（实现 trait）

#### A4. `ModelAdapter` — 模型格式适配

- **位置**：`crates/arf-model-adapter/src/node.rs`（`ModelAdapterNode`）
- **职责**：框架内消息 ↔ 外部 API 格式；处理 `tool_result → model_message` 转换；可插拔 provider
- **provider 类型**（各自 Config）：`DeepSeekProvider`（`DeepSeekConfig`） · `OpenAIProvider`（`OpenAIConfig`） · `AnthropicProvider`（`AnthropicConfig`） · `MiniMaxProvider`（`MiniMaxConfig`）；所有实现 `Provider` trait
- **关键字段**：`node_id: NodeId` · `provider: Arc<dyn Provider>` · `cancel: CancellationToken` · `_loop_handle: JoinHandle`
- **订阅**：connect 时 `MessageFilter { types: Some(vec!["model_call"]), to_match: ToMatch::BroadcastAndDirectedToMe }`
- **pool 变体**：`ModelAdapterPoolNode`（`crates/arf-model-adapter/src/pool_node.rs`）+ `ModelAdapterResource`（`crates/arf-model-adapter/src/pool_resource.rs`）
- **生命周期**：`ModelAdapterNode::new(...).start(bus)` 后 loop 监听 `model_call` → 调 Provider → publish `model_response`；`shutdown()` 通过 cancel token 终止
- **典型交互**：`ModelAdapter → Bus`（publish `model_response`） · `Engine → ModelAdapter`（subscribe `model_call`） · `ModelAdapter → Provider`（HTTP 请求） · `convert: ToolResultItem → ModelMessage`（`crates/arf-model-adapter/src/convert.rs`，依赖 `arf-mcp::types::ToolResultItem`）

#### A5. `State` — 状态机状态

- **位置**：`crates/arf-core/src/state.rs`
- **职责**：Engine-owned Agent 状态；App 持有 `State`，Engine 借 `&mut`；持久化由 App 负责（`Engine.snapshot()` / `Engine.restore()`）
- **关键字段**：`messages: Vec<ModelMessage>` · `over_view: OverView` · `wait_events: Vec<WaitEvent>`
- **⚠️ 与 v1.x design 差异**：design 草稿提到 `messages + tasks + over_view`，**当前实现是 `messages + over_view + wait_events`**（用 `wait_events` 代替 `tasks`）
- **`OverView` 子字段**：`round_count: usize` · `turn_count: usize` · `context_tokens: usize` · `model_context_window: usize` · `runtime: Duration` · `last_user_message: String` · 提供 `context_utilization() -> f64`
- **生命周期**：App 创建 `State::new()`；每次 `chat()` 期间 Engine 借 `&mut`；snapshot/restore 由 App 触发
- **典型交互**：`Engine → State`（push_message / inc_round / inc_turn / set_context_tokens / push_wait_event / pop_wait_event） · `App → State`（snapshot / restore）

#### A6. `AgentConfig` — 声明式配置骨架

- **位置**：`crates/arf-agent/src/config.rs`（**不在 arf-engine**）；`arf-engine/src/config.rs` 仅提供嵌套的 `EngineConfig`
- **职责**：纯数据结构；声明 agent 需要的全部资源（model + resources）；不引用 NodeId，不感知 Bus；Engine 负责解析和驱动
- **关键字段**：`model: ModelDecl` · `resources: Vec<ResourceSpec>` · `system_prompt_template` · `initial_memory` · `allowed_paths` · `engine: EngineConfig`
- **生命周期**：App 构造一次性传入 `EngineBuilder::new(buses).build(config)`；build 后被 ResourceRegistry 物化
- **典型交互**：`App → AgentConfig`（构造） · `EngineBuilder → AgentConfig`（解析为 NodeId） · `ResourceSpec → AgentConfig`（被持有）

#### A6.1 `EngineConfig` — 嵌套运行期配置

- **位置**：`crates/arf-engine/src/config.rs`（嵌套在 `AgentConfig.engine` 字段内）
- **关键字段**：`routes: HashMap<String, Route>` · `checkpoint_rules: Vec<CheckpointRule>` · `processors: HashMap<String, Arc<dyn ResponseProcessor>>` · `on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>` · `max_turns: u32`（默认 10） · `tool_timeout_ms: Option<u64>`（默认 30_000）
- **默认值**：`routes={}` · `checkpoint_rules=[]` · `processors={}` · `on_member_failed=None` · `max_turns=10` · `tool_timeout_ms=30_000`
- **典型交互**：`AgentConfig → EngineConfig`（嵌套持有） · `Engine → EngineConfig`（每轮读取 routes / checkpoint_rules / processors）

#### A6.2 `OnMemberFailedHandler` — 节点掉线策略 trait

- **位置**：`crates/arf-engine/src/config.rs`
- **方法**：`fn handle(agent: &NodeId, member: &NodeId, reason: &str) -> MemberFailedAction`
- **默认实现**：函数闭包 `Fn(&NodeId, &NodeId, &str) -> MemberFailedAction + Send + Sync`
- **返回动作**：见 `MemberFailedAction`
- **典型交互**：`EngineConfig → OnMemberFailedHandler`（Option 持有） · `Engine → OnMemberFailedHandler`（节点掉线时回调）

#### A6.3 `MemberFailedAction` — 掉线动作 enum

- **位置**：`crates/arf-engine/src/config.rs`
- **变体**：`FailSession`（终止 session）· `Retry { delay_ms: u64 }`（延迟重试）· `SwitchTo { alternative: NodeId }`（切换到备选节点）
- **默认**：`FailSession`
- **典型交互**：`OnMemberFailedHandler → MemberFailedAction`（handler 返回） · `Engine → MemberFailedAction`（按动作执行）

### B 组：Phase 6 协议层抽象（5 实体）

#### B1. `Node` — Bus 参与者 trait

- **职责**：Bus 上对等参与者的统一契约；`id()` · `snapshot()` · `restore()` · `on_message(_, from_bus: BusId)` 四方法；Engine 自己也是 Node
- **关键方法**：`id() -> NodeId` · `snapshot() -> Value` · `restore(&mut self, value)` · `on_message(&mut self, msg, from_bus)`
- **生命周期**：所有 Bus 注册者实现该 trait；Engine / MCP / ModelAdapter / 自定义节点都实现
- **典型交互**：`Node → Bus`（connect / disconnect） · `Bus → Node`（push msg） · `Node ↔ Node`（通过 Bus 中转）

#### B2. `ActionMessage` — 可扩展消息协议 trait

- **职责**：应用层消息载荷的统一契约；定义 `msg_type`（路由 key）+ `correlation_id`（响应匹配）+ `payload`（序列化）+ `intent`（Query / Command）
- **关键方法**：`msg_type() -> &'static str` · `correlation_id() -> Uuid` · `payload() -> Value` · `intent() -> MessageIntent`
- **生命周期**：随消息生成 / 序列化 / 消费；Engine 通过 `ResponseProcessor` 桥接到 `Response`
- **典型交互**：`App → ActionMessage`（实现自定义 msg_type） · `Engine → ActionMessage`（publish + await response）

#### B3. `Route` — 路由决策 enum

- **职责**：单个 msg_type 的投递方式；二元变体：`Strict(Vec<NodeId>)` 定向 · `Discovery(Capability)` 按能力发现；**无 Any 模式**
- **关键字段**：`Route::Strict(Vec<NodeId>)` / `Route::Discovery(Capability)`
- **生命周期**：构建期由 App 写入 `AgentConfig.engine.routes`；运行时由 Engine / Bus 查表
- **典型交互**：`App → Route`（声明） · `Engine → Route`（按 msg_type 查表） · `Capability → NodeId`（Bus 解析 Discovery → 节点列表）

#### B4. `CheckpointRule` — 触发器 + 消息构建

- **职责**：Engine 在 5 个固定位置（Checkpoint）上的可订阅触发器；4 元组：`name + trigger + when + build`；**不含 route**（routes 由 `AgentConfig.routes` 单一来源）
- **关键字段**：`name` · `trigger: Checkpoint` · `when: Box<dyn Fn(&State) -> bool>` · `build: Box<dyn Fn(&State) -> Box<dyn ActionMessage>>`
- **生命周期**：App 通过 `EngineBuilder.checkpoint_rule(rule)` 注册；Engine.run 每轮检查 `fires(state)`
- **典型交互**：`App → CheckpointRule`（注册） · `Engine → CheckpointRule`（fires() / build_msg()）

#### B5. `Checkpoint` — 5 个固定位置 enum

- **职责**：ReAct 循环中 Engine 暴露的可干预点；5 个不变量位置：`BeforeModelCall` / `AfterModelCall` / `BeforeToolExec` / `AfterToolExec` / `RoundEnd`
- **关键字段**：5 个 enum variant
- **生命周期**：编译期固定；运行时仅作为 `CheckpointRule.trigger` 的取值
- **典型交互**：`Engine → Checkpoint`（在循环内触发检查） · `CheckpointRule → Checkpoint`（触发位置匹配）

### C 组：Phase 7 资源装配层（7 实体）

#### C1. `ResourceSpec` — 资源声明载体

- **位置**：`crates/arf-agent/src/resource.rs`
- **职责**：Agent 给资源的逻辑名 + 类型 + 能力过滤；Engine 解析为 NodeId
- **关键字段**：`resource_name: String`（**注意：不是 `name`**；带 `#[serde(alias = "name")]` 向后兼容）· `node_type: String` · `capabilities: Option<Value>`
- **capabilities 语义**（双层）：
  - **arf-agent 定义**：subset check — node 的 capabilities 包含 spec 中的所有 keys/values 即匹配；`None` 匹配任意该 node_type 节点
  - **phase7 resource-registry 设计**：`DeclaredFilter::All | Subset(Vec<String>) | None_` 三态（Subset 时按 name 命中至少一个 tool/skill 避免两条 spec 同时命中第一个 MCP）
  - **当前实现**：phase7 设计尚未全部合入，agent 类型只声明 subset 语义，registry 端实现三态过滤
- **常见 node_type**：`"mcp"` · `"mcp/pool"` · `"agent/subagent"` · `"agent/teammate"`
- **生命周期**：在 `AgentConfig.resources` 中持有；build 期被 Engine 解析；runtime immutable
- **典型交互**：`App → ResourceSpec`（构造） · `ResourceRegistry → ResourceSpec`（保存 build 期快照） · `Engine → ResourceSpec`（capabilities 过滤 MCP 全集）

#### C1.1 `ToolPermission` — 工具权限 enum

- **位置**：`crates/arf-agent/src/tool.rs`
- **变体**：`Allow`（自动执行） · `Ask`（需用户确认） · `Deny`（拒绝执行）
- **职责**：Engine 在 runtime 强制执行 Tool 调用前的权限校验
- **典型交互**：`Engine → ToolPermission`（tool_exec 派发前检查） · `App → ToolPermission`（在 arf_agent::ToolSpec 中声明）

#### C1.2 `ToolSpec` — 双类型

> ⚠️ **命名陷阱**：`arf-core` 与 `arf-agent` 都定义了 `ToolSpec`，语义不同，使用时需区分。

| 维度 | `arf_core::ToolSpec` | `arf_agent::ToolSpec` |
|------|----------------------|------------------------|
| **位置** | `crates/arf-core/src/tool.rs` | `crates/arf-agent/src/tool.rs` |
| **用途** | LLM function-calling 注入到 `ModelCall.tools` | Agent 侧声明（带权限 + 过滤） |
| **字段** | `name` · `description` · `parameters: Value` | `name` · `permission: ToolPermission` · `parameter_filter: Option<Value>` · `description: Option<String>` · `parameters: Option<Value>` |
| **谁用** | Engine 在 ReAct 主循环构造 | App 在 AgentConfig.tools 中声明 |

#### C1.3 `EngineBuilder` — 实际 API（与设计稿有差）

- **位置**：`crates/arf-engine/src/builder.rs`
- **构造**：`EngineBuilder::new(buses: Vec<Arc<Bus>>)`（**接受多 Bus**，不是单 Bus）
- **build**：`async fn build(self, config: AgentConfig) -> Result<Engine, BuildError>`
- **不是链式 API**（与原 phase7 design 草案不同）：所有声明（resources / checkpoint_rules / routes）都在 `AgentConfig` 内一次性传入；EngineBuilder 不暴露 `add_resource()` / `checkpoint_rule()` 等方法
- **build 期 4 步骤**：
  1. 聚合多 Bus graph → `BusGraph` snapshot
  2. `ResourceRegistry::build(&config, &snapshot)?`
  3. 校验 `config.engine.routes` 中 Strict 路由的所有 NodeId 在 graph 中
  4. 校验 `config.engine.checkpoint_rules` name 不重复（重复 → `BuildError::DuplicateRuleName`）
- **典型交互**：`App → EngineBuilder`（`new(buses).build(config)`） · `EngineBuilder → Bus`（snapshot） · `EngineBuilder → ResourceRegistry`（构造）

#### C2. `ResourceRegistry` — 资源声明 → NodeId 映射表

- **位置**：`crates/arf-engine/src/registry.rs`（`pub(crate)`，Engine 内部使用）
- **职责**：build 期物化后的静态映射；包含三部分：model NodeId · MCP bindings（含 DeclaredFilter） · custom bindings · tool_index（防重名）· skills_cache
- **关键字段**：`model: NodeId` · `mcp_nodes: HashMap<NodeId, ResourceBinding>` · `custom_nodes: HashMap<NodeId, ResourceBinding>` · `tool_index: HashMap<String, NodeId>` · `skills_cache: Mutex<(u64, String)>`
- **生命周期**：`EngineBuilder.build()` 末尾构造；随 Engine 存在；build 后 immutable
- **典型交互**：`EngineBuilder → ResourceRegistry`（构造） · `Engine → ResourceRegistry`（target_of / tools_for_model / skills_text）

#### C3. `DiscoveryCache` — capability → recipients 缓存

- **位置**：`crates/arf-engine/src/checkpoint.rs`
- **职责**：避免每次 publish 重复扫 Bus 图
- **关键字段**：`Mutex<HashMap<Vec<(String, String)>, Vec<NodeId>>>`（key 是 `Capability.requirements` 的 clone，**不含 TTL**）
- **失效**：监听 Bus graph 变更（`node_online` / `node_offline`），调用 `invalidate()` 清空
- **生命周期**：Engine 内 `Arc<DiscoveryCache>`；Engine::new 时 spawn lifecycle listener
- **典型交互**：`Engine → DiscoveryCache`（`get_or_compute` / `invalidate`） · `Bus → DiscoveryCache`（订阅 `node_online/offline` 触发 invalidate）

#### C4. `NodeHandle` — Node 在 Bus 上的句柄

- **位置**：`crates/arf-bus/src/connection.rs`
- **职责**：Node 注册到 Bus 后获得的句柄；支持**多 Bus 订阅**（`attach_to(bus, filter)`）；底层每个订阅一个 mpsc channel + forwarding task
- **关键方法**：`send(msg) -> Result<SendReceipt, SendError>`（发到 primary Bus）· `send_via(bus_id, msg)`（发到指定 Bus）· `attach_to(bus, filter)`（订阅额外 Bus）· `recv() -> Result<Message, RecvError>`（用 `futures::select_all` 跨所有订阅读）· `disconnect()`
- **内部结构**：`info: NodeInfo` · `primary_bus_id: BusId` · `subscriptions: Vec<Subscription>`（primary 在前）
- **生命周期**：随 Node 存在；Node 销毁时自动 disconnect（所有 forwarding task 退出 → 所有 inbound channel 关闭 → recv() 返回 Closed）
- **典型交互**：`Node → NodeHandle`（持有） · `Engine → NodeHandle`（publish model_call / tool_exec） · `Bus → NodeHandle`（push msg via forwarding task → inbound mpsc）

### D 组：原子资源类型（4 实体）

#### D1. `Model` — 大语言模型资源

- **职责**：原子资源；thinking / reasoning engine；通过 ModelAdapter 节点对外暴露
- **关键字段**：`provider` · `model_name` · `thinking_enabled` · `temperature` · `max_output_tokens`
- **生命周期**：在 `AgentConfig.model: ModelDecl` 中声明；build 期绑定到 ModelAdapter NodeId
- **典型交互**：`App → Model`（通过 ModelDecl 声明） · `Engine → Model`（通过 ModelAdapter publish model_call）

#### D2. `Tool` — 可执行函数资源

- **职责**：原子资源；可被 LLM function-calling 调用；通过 MCP namespace 暴露；runtime 支持 Python / Bash / Rust
- **关键字段**：`name` · `description` · `parameters: JSON Schema` · `runtime` · `entrypoint`
- **生命周期**：通过 `{root}/tools/*/tool.toml` 扫描发现；MCP namespace 内 `HashMap<String, ToolDef>` 持有
- **典型交互**：`App → Tool`（放置 tool.toml 文件） · `Engine → Tool`（publish tool_exec） · `MCP → Tool`（通过 RuntimeModule 执行）

#### D3. `Skill` — 领域知识 / 按需加载指令

- **职责**：原子资源；Markdown 文件 + YAML frontmatter；渐进披露 L1（列表）/ L2（描述）/ L3（完整内容）
- **关键字段**：`name` · `description` · `body: Markdown` · `level: L1 | L2 | L3`
- **生命周期**：通过 `{root}/skills/*/SKILL.md` 扫描发现；仅本地 MCP 节点持有
- **典型交互**：`MCP → Skill`（通过 `DiscoveryBackend::load_skill_body` / `list_skills` / `resolve_skill` 加载；**非 msg_type**） · `Engine → Skill`（构造 system_prompt 时通过 `ResourceRegistry.skills_text()` 拉 L2 文本）

#### D4. `Hook` — 生命周期事件处理器

- **职责**：原子资源；session/round/turn 边界的事件观察者；**当前实现：CheckpointRule 即 Hook 的实现形态**（hook 没有独立 Node，只通过 CheckpointRule 表达）
- **关键字段**：见 `CheckpointRule`
- **生命周期**：通过 `EngineBuilder.checkpoint_rule(rule)` 注册
- **典型交互**：`Engine → Hook`（Checkpoint 位置触发） · `App → Hook`（注册自定义规则）

### E 组：用户侧抽象层（3 实体）

> 这三个是 CLAUDE.md 约定的用户视角分层，**不是 Rust 类型**，是 session/round/turn 时间维度的抽象。

#### E1. `Session` — 多轮对话会话

- **职责**：用户视角最高层；一个 session 包含多 round；有独立 state_store + trace 文件 + session_id
- **关键字段**：`session_id` · `state: SessionState` · `trace_file: Path`
- **生命周期**：App 创建；持久化到 state_store；可中断 / 恢复
- **典型交互**：`App → Session`（创建 / chat / snapshot） · `Session → State`（持有 per-session State）

#### E2. `Round` — 单轮 user → final

- **职责**：一次 `user_input` 到 `final_output` 的完整流程；含若干 turn
- **关键字段**：`round_idx` · `user_input` · `final_output` · `turns: Vec<Turn>`
- **生命周期**：每次 `chat()` 调用 = 一个 round
- **典型交互**：`Session → Round`（创建） · `Engine → Round`（驱动 turn 循环）

#### E3. `Turn` — ReAct 单步

- **职责**：一次 model_call [+ tool_calls]；最后一步可能仅 model_call 无 tool_calls
- **关键字段**：`turn_idx` · `model_call` · `tool_calls: Vec<ToolCall>` · `model_response`
- **生命周期**：在 Round 内的 ReAct 循环中
- **典型交互**：`Round → Turn`（创建） · `Engine → Turn`（驱动）

---

## §2 19×19 全量交互表

> **约定**：
> - 行 = 发送方 / 操作主体，列 = 接收方 / 操作客体
> - 每格列出 row → column 所有可能的交互模式
> - 拓扑符号见 §3
> - "—" 表示该对实体无任何直接交互（Engine 不感知 / Node trait 不实现 / 等）

### §2.1 A 组内交互（6×6 = 36 格）

| ↓ 行 \\ → 列 | `Bus` | `Engine` | `MCP` | `ModelAdapter` | `State` | `AgentConfig` |
|---|---|---|---|---|---|---|
| **`Bus`** | **自交互**：[S1] | [S2] | [S3] | [S4] | — | — |
| **`Engine`** | [E1] | **自交互**：[E2] | [E3] | [E4] | [E5] | [E6] |
| **`MCP`** | [M1] | [M2] | **自交互**：[M3] | — | — | — |
| **`ModelAdapter`** | [MA1] | [MA2] | [MA3] | **自交互**：[MA4] | — | [MA5] |
| **`State`** | — | [ST1] | — | — | **自交互**：[ST2] | — |
| **`AgentConfig`** | — | [AC1] | — | — | — | **自交互**：[AC2] |

#### [S1] `Bus ↻ Bus`（自交互）
1. **N↔N 广播路由**（`to: Vec::new()` → 全在线节点；底层 `tokio::sync::broadcast`）
2. **p2p 定向路由**（`to: Vec<NodeId>` → 校验在线后定向投递）
3. **N↔N 心跳维护**（定期广播 `heartbeat_request` msg；NodeHandle 自动 ack；超时标记 offline）
4. **N↔N node_online/offline 通知**（`BusCommand::Connect`/`Disconnect` 触发 `node_online`/`node_offline` 广播）
5. **N↔N graph 一致性**（`bus.graph()` 返回 `BusGraph { nodes, message_count, uptime_ms }`）
6. **N↔N slow consumer 处理**（broadcast 通道使用 `Lagged(n)` 机制，慢消费者不会阻塞发送者或其他快消费者；CAN 模型）
7. **N↔N drain_rx 内部守护**（永久持有 broadcast::Receiver 保持 `receiver_count >= 1`，消除 SendError 路径）

#### [S2] `Bus → Engine`
1. **N→1 push msg**（将符合 Engine MessageFilter 的消息推到 Engine 的 mpsc inbox）

#### [S3] `Bus → MCP`
1. **N→1 push msg**（将 tool_exec / tool_call_set 等推到对应 MCP）

#### [S4] `Bus → ModelAdapter`
1. **N→1 push msg**（将 model_call 推到对应 ModelAdapter）

#### [E1] `Engine → Bus`
1. **1→1 publish model_call**（`msg_type = "model_call"`，定向到 ModelAdapter）
2. **1→1 publish tool_exec**（`msg_type = "tool_exec"`，定向到 MCP）
3. **1→1 publish tool_call_set**（DAG 派发，`ToolCallSet` 含每项 `blocked_by`/`blocking` 双向锁）
4. **1→N broadcast checkpoint msg**（CheckpointRule 派发的自定义 msg_type）
5. **1→1 publish tool_result_to_model_message**（MCP 工具结果 → ModelAdapter 格式转换）
6. **1→1 connect**（启动时 Engine 作为 Node 注册到 Bus）
7. **1→1 disconnect**（stop 时清理）

#### [E2] `Engine ↻ Engine`（自交互）
1. **p2p 点对点**（队友私聊 — 跨 session 定向消息，例 `to = vec![peer_engine]`）
2. **1→N 广播**（分配子任务给 teammates）
3. **N→1 汇总**（接收 subagent 结果汇报）
4. **park/resume**（等待响应 → 恢复执行，WaitEvent 机制）
5. **delegate_task**（Engine 调 subagent tool 触发嵌套 ReAct）

#### [E3] `Engine → MCP`
1. **1→1 publish tool_exec**（单 tool 调用，Engine 内置白名单；MCP 节点 `dispatch` 中按 `tool_name` 过滤，只 owner 响应）
2. **1→1 publish tool_call_set**（DAG 批量调用，含双向锁）
3. **1→N subscribe node_online/offline**（通过 Bus lifecycle listener 监控）

#### [E4] `Engine → ModelAdapter`
1. **1→1 publish model_call**（构造 `ModelCall { messages, tools }`，定向）
2. **1→1 park until model_response**（`intent = Query`，等待 correlation_id 匹配）

#### [E5] `Engine → State`
1. **1→1 push_message**（用户 / 助手 / 工具消息追加）
2. **1→1 inc_round**（每次 chat 末尾 +1）
3. **1→1 inc_turn**（每次 ReAct 转移步 +1）
4. **1→1 set_context_tokens**（从 model_response.usage 更新）
5. **1→1 push_wait_event**（publish 时新建）
6. **1→1 pop_wait_event**（response 到达时移除）

#### [E6] `Engine → AgentConfig`
1. **1→1 读取**（build 期一次性 parse；runtime immutable 不修改）

#### [M1] `MCP → Bus`
1. **1→1 connect**（注册 MCP node + `MessageFilter { types: None, to_match: ToMatch::All }` + capabilities 含 `runtime` + `tools` + `skills`）
2. **1→1 publish tool_result**（`msg_type = "tool_result"`，响应 Engine `tool_exec`）
3. **1→1 publish tool_result_set**（`msg_type = "tool_result_set"`，响应 Engine `tool_call_set`；MCP 节点 `dispatch` 返回 `(msg_type, payload)`）
4. **1→1 disconnect**（runtime 关闭时清理）
5. **MCP 节点 message_loop 过滤**：每个 msg 检查 `is_for(&self.node_id) || is_broadcast()`，不相关 msg continue

#### [M2] `MCP → Engine`
1. **N→1 tool_result 返回**（响应 `tool_exec`）
2. **N→1 tool_result_set 返回**（响应 `tool_call_set`）

#### [M3] `MCP ↻ MCP`（自交互）
1. **N↔N namespace 隔离**（`mcp/filesystem/read_file` ≠ `mcp/network/read_file`）
2. **N→1 冲突检测**（同 namespace 内重名 → 开发期 panic）
3. **p2p 跨 namespace 协作**（跨 namespace DAG 依赖）
4. **N↔N pool 内部 sub-bus**（`mcp/pool` 类型内部 worker 调度）

#### [MA1] `ModelAdapter → Bus`
1. **1→1 connect**（注册 ModelAdapter node + capabilities 含 `kind=model, provider`）
2. **1→1 publish model_response**（`msg_type = "model_response"`，content + tool_calls + usage）
3. **1→1 disconnect**

#### [MA2] `ModelAdapter → Engine`
1. **N→1 model_response 返回**（定向到 Engine）

#### [MA3] `ModelAdapter → MCP`
1. **N→1 读 tool_result_to_model_message**（反向依赖：`arf-model-adapter → arf-mcp` 单向；MCP 产出纯数据 `ToolResultItem`，ModelAdapter 转换）

#### [MA4] `ModelAdapter ↻ ModelAdapter`（自交互）
1. **p2p fallback 切换**（同 agent 多 provider 配置时，第一个 online 的用，超时切下一个）
2. **N↔N provider pool**（同 provider 多实例负载均衡）

#### [MA5] `ModelAdapter → AgentConfig`
1. **N→1 读取 ModelDecl**（build 期一次性读取 endpoint / api_key_env / thinking_enabled / temperature 等）

#### [ST1] `State → Engine`
1. **1→1 snapshot 提供**（Engine.snapshot() 时读 State 的 clone）
2. **1→1 restore 接收**（Engine.restore(value) 时写回 State）

#### [ST2] `State ↻ State`（自交互）
1. **1→1 push_message 顺序保证**（Vec 追加，遵守用户/助手交替顺序）
2. **1→1 last_user_message 维护**（user 角色时同步更新 OverView）
3. **1→1 round/turn counter 单调递增**
4. **1→1 wait_events 生命周期**（push 时按 expected 增加，response 到达时按 strategy 触发并 pop）

#### [AC1] `AgentConfig → Engine`
1. **1→1 传入 EngineBuilder**（build(config) 一次性消费）

#### [AC2] `AgentConfig ↻ AgentConfig`（自交互）
1. **1→1 nested 嵌套**（`AgentConfig { ..declaration, engine: EngineConfig }`）
2. **1→1 ModelDecl 持有**（单 model 声明）
3. **1→N ResourceSpec 持有**（资源声明列表）
4. **1→1 EngineConfig.routes 持有**（自定义 msg_type 路由表）

---

### §2.2 A ↔ B 跨组交互（6×5 = 30 格）

| ↓ 行 \\ → 列 | `Node` | `ActionMessage` | `Route` | `CheckpointRule` | `Checkpoint` |
|---|---|---|---|---|---|
| **`Bus`** | [S-B1] | [S-B2] | [S-B3] | — | — |
| **`Engine`** | [E-B1] | [E-B2] | [E-B3] | [E-B4] | [E-B5] |
| **`MCP`** | [M-B1] | [M-B2] | — | — | — |
| **`ModelAdapter`** | [MA-B1] | [MA-B2] | — | — | — |
| **`State`** | — | — | — | — | — |
| **`AgentConfig`** | — | — | [AC-B1] | [AC-B2] | [AC-B3] |

#### [S-B1] `Bus → Node`
1. **N→1 push msg**（通过 NodeHandle.mpsc 推送到 Node.on_message）

#### [S-B2] `Bus → ActionMessage`
1. **N→1 wire 序列化**（通过 `ActionMessage::payload()` 序列化 Message.payload）

#### [S-B3] `Bus → Route`
1. **N→1 Route 解析**（Strict → 直接查 nodes；Discovery → 扫描 nodes 找 capability 匹配）

#### [E-B1] `Engine → Node`
1. **1→1 implements Node**（Engine 自身作为 Node 注册）
2. **1→N 通过 Bus 中转**（Engine 不直接调其他 Node 的 on_message）

#### [E-B2] `Engine → ActionMessage`
1. **1→1 构造 ModelCall**（Checkpoint BeforeModelCall → publish）
2. **1→1 构造 ToolExec**（Checkpoint BeforeToolExec → publish）
3. **1→N 构造 checkpoint 自定义 msg**（CheckpointRule.build 返回 Box<dyn ActionMessage>）

#### [E-B3] `Engine → Route`
1. **1→1 按 msg_type 查表**（`AgentConfig.routes.get(msg_type)`）
2. **1→1 默认路由**（model_call / tool_exec 的路由从 ResourceRegistry 推导）
3. **1→1 DiscoveryCache 缓存**（Discovery 路由查询经缓存加速）

#### [E-B4] `Engine → CheckpointRule`
1. **1→N 触发 fires(state) 检查**（5 个 Checkpoint 位置每个都遍历注册表）
2. **1→1 调 build_msg(state) 获取 ActionMessage**（fires 返回 true 时）
3. **1→1 调 OnMemberFailedHandler**（CheckpointRule 内部出错时的回调）

#### [E-B5] `Engine → Checkpoint`
1. **1→1 match trigger**（switch on 5 个 Checkpoint variant）
2. **1→1 串行触发**（BeforeModelCall → model_call publish → AfterModelCall → ...）

#### [M-B1] `MCP → Node`
1. **1→1 implements Node**（MCP 节点实现 Node trait）

#### [M-B2] `MCP → ActionMessage`
1. **1→1 ToolResultItem 转换**（ToolResultItem → ModelMessage 是 ModelAdapter 职责，MCP 只产出纯数据）
2. **N↔N 自定义 msg_type 实现**（MCP 可实现自定义 ActionMessage 扩展，例 batch_tool_exec）

#### [MA-B1] `ModelAdapter → Node`
1. **1→1 implements Node**（ModelAdapter 节点实现 Node trait）

#### [MA-B2] `ModelAdapter → ActionMessage`
1. **1→1 构造 ModelResponse**（不是标准 ActionMessage，是 Engine 内置响应类型；Engine 直接 dispatch）
2. **1→1 tool_result_to_model_message 转换**（把 ToolResultItem 包成 ModelMessage，注入到下一轮 messages）

#### [AC-B1] `AgentConfig → Route`
1. **1→N 持有 EngineConfig.routes**（`HashMap<String, Route>` 自定义 msg_type 路由）

#### [AC-B2] `AgentConfig → CheckpointRule`
1. **1→N 持有 checkpoint_rules**（`Vec<CheckpointRule>` App 注册的所有规则）

#### [AC-B3] `AgentConfig → Checkpoint`
1. **1→1 通过 CheckpointRule.trigger 引用**（间接持有）

---

### §2.3 A ↔ C 跨组交互（6×5 = 30 格）

| ↓ 行 \\ → 列 | `ResourceSpec` | `ResourceRegistry` | `EngineBuilder` | `DiscoveryCache` | `NodeHandle` |
|---|---|---|---|---|---|
| **`Bus`** | — | — | [S-C1] | [S-C2] | [S-C3] |
| **`Engine`** | [E-C1] | [E-C2] | [E-C3] | [E-C4] | [E-C5] |
| **`MCP`** | [M-C1] | — | — | — | [M-C2] |
| **`ModelAdapter`** | [MA-C1] | — | — | — | [MA-C2] |
| **`State`** | — | — | — | — | — |
| **`AgentConfig`** | [AC-C1] | — | [AC-C2] | — | — |

#### [S-C1] `Bus → EngineBuilder`
1. **N→1 snapshot 响应**（build 期 EngineBuilder 调 `bus.snapshot()` 拿当前节点图）

#### [S-C2] `Bus → DiscoveryCache`
1. **N→1 graph 变更通知**（node_online/offline 触发 invalidate）

#### [S-C3] `Bus → NodeHandle`
1. **1→1 返回 NodeHandle**（connect() 成功后返回）

#### [E-C1] `Engine → ResourceSpec`
1. **1→1 读取 capabilities**（构建 ModelCall.tools 时按 spec.capabilities 过滤）

#### [E-C2] `Engine → ResourceRegistry`
1. **1→N target_of 查询**（每次 publish 前查 `to`）
2. **1→N tools_for_model 查询**（构造 ModelCall.tools 时）
3. **1→N skills_text 查询**（构造 system_prompt 时）
4. **1→1 send fail-fast**（`target_of` 返回 None 或 `SendError::NodeOffline` → 立即 fail）

#### [E-C3] `Engine → EngineBuilder`
1. **1→1 build 结果**（EngineBuilder.build() 成功时构造 Engine）

#### [E-C4] `Engine → DiscoveryCache`
1. **1→N resolve(capability) 查询**（Discovery 路由时）
2. **1→1 invalidate**（监听 Bus graph 变更）

#### [E-C5] `Engine → NodeHandle`
1. **1→1 持有**（Engine 启动时存 `handle: NodeHandle`）
2. **1→1 publish(msg) 调用**
3. **1→1 inbox() 收消息**

#### [M-C1] `MCP → ResourceSpec`
1. **N→1 capabilities 自描述**（NodeInfo.capabilities 暴露 MCP 全集 tool/skill 名）

#### [M-C2] `MCP → NodeHandle`
1. **1→1 持有**（MCP connect 时存 handle）
2. **1→1 publish tool_result**
3. **1→1 inbox() 收 tool_exec**

#### [MA-C1] `ModelAdapter → ResourceSpec`
1. **N→1 capabilities 自描述**（NodeInfo.capabilities 含 `kind=model, provider`）

#### [MA-C2] `ModelAdapter → NodeHandle`
1. **1→1 持有**
2. **1→1 publish model_response**

#### [AC-C1] `AgentConfig → ResourceSpec`
1. **1→N 持有 resources 列表**

#### [AC-C2] `AgentConfig → EngineBuilder`
1. **1→1 传入 build(config)**

---

### §2.4 A ↔ D 跨组交互（6×4 = 24 格）

| ↓ 行 \\ → 列 | `Model` | `Tool` | `Skill` | `Hook` |
|---|---|---|---|---|
| **`Bus`** | — | — | — | — |
| **`Engine`** | [E-D1] | [E-D2] | [E-D3] | [E-D4] |
| **`MCP`** | — | [M-D1] | [M-D2] | — |
| **`ModelAdapter`** | [MA-D1] | — | — | — |
| **`State`** | — | — | — | — |
| **`AgentConfig`** | [AC-D1] | [AC-D2] | [AC-D3] | [AC-D4] |

#### [E-D1] `Engine → Model`
1. **1→1 通过 ModelAdapter 调 Model**（publish model_call）

#### [E-D2] `Engine → Tool`
1. **1→N ToolSpec 注入 ModelCall.tools**（仅 ToolSpec 形态，Engine 不直接执行 Tool）
2. **N↔N 模型 function-calling → tool_exec**

#### [E-D3] `Engine → Skill`
1. **1→N Skill L2 注入 system_prompt**（从 `ResourceRegistry.skills_text()` 读取）
2. **1→1 通过 MCP `DiscoveryBackend` trait 加载**（`load_skill_body` / `list_skills` / `resolve_skill`）—— **非 msg_type**

#### [E-D4] `Engine → Hook`
1. **1→N CheckpointRule 触发**（Hook 通过 CheckpointRule 形态实现）

#### [M-D1] `MCP → Tool`
1. **1→N RuntimeModule 执行**（stdin/stdout JSON 协议）
2. **1→1 tool.toml 解析 + 加载**

#### [M-D2] `MCP → Skill`
1. **1→N SKILL.md 解析 + L1/L2/L3 渐进披露**
2. **1→N skills/* 目录扫描发现**

#### [MA-D1] `ModelAdapter → Model`
1. **1→1 HTTP 请求 → API**（OpenAI / DeepSeek / Anthropic / MiniMax 等）

#### [AC-D1] `AgentConfig → Model`
1. **1→1 ModelDecl 持有**

#### [AC-D2] `AgentConfig → Tool`
1. **N↔N 通过 ResourceSpec 间接声明**（capabilities.tools 过滤）

#### [AC-D3] `AgentConfig → Skill`
1. **N↔N 通过 ResourceSpec 间接声明**（capabilities.skills 过滤）

#### [AC-D4] `AgentConfig → Hook`
1. **N↔N 通过 CheckpointRule 间接声明**（engine.checkpoint_rules 持有）

---

### §2.5 A ↔ E 跨组交互（6×3 = 18 格）

| ↓ 行 \\ → 列 | `Session` | `Round` | `Turn` |
|---|---|---|---|
| **`Bus`** | — | — | — |
| **`Engine`** | [E-E1] | [E-E2] | [E-E3] |
| **`MCP`** | — | — | — |
| **`ModelAdapter`** | — | — | — |
| **`State`** | [ST-E1] | [ST-E2] | [ST-E3] |
| **`AgentConfig`** | [AC-E1] | — | — |

#### [E-E1] `Engine → Session`
1. **1→1 跨 session p2p**（`to = vec![peer_session_engine]`）
2. **1→1 session_id 注入 Message.from**（每个 msg 带 session_id 便于过滤）

#### [E-E2] `Engine → Round`
1. **1→1 驱动 Round 边界**（chat() 调用 = 1 round，RoundEnd checkpoint 触发）
2. **1→1 State.over_view.round_count++**

#### [E-E3] `Engine → Turn`
1. **1→1 驱动 Turn 边界**（每次 ReAct 转移步 = 1 turn）
2. **1→1 State.over_view.turn_count++**

#### [ST-E1] `State → Session`
1. **1→1 per-session State 实例**

#### [ST-E2] `State → Round`
1. **1→1 messages per round**（Vec 累积）
2. **1→1 round_count in OverView**

#### [ST-E3] `State → Turn`
1. **1→1 turn_count in OverView**
2. **1→1 messages 按 turn 追加**

#### [AC-E1] `AgentConfig → Session`
1. **1→1 per-session EngineConfig**（同一个 AgentConfig 可驱动多个 Session）

---

### §2.6 B 组内交互（5×5 = 25 格）

| ↓ 行 \\ → 列 | `Node` | `ActionMessage` | `Route` | `CheckpointRule` | `Checkpoint` |
|---|---|---|---|---|---|
| **`Node`** | **自交互**：[N1] | [N2] | [N3] | — | — |
| **`ActionMessage`** | [AM1] | **自交互**：[AM2] | [AM3] | — | — |
| **`Route`** | [R1] | [R2] | **自交互**：[R3] | — | — |
| **`CheckpointRule`** | [CR1] | [CR2] | — | **自交互**：[CR3] | [CR4] |
| **`Checkpoint`** | — | — | — | [CP1] | **自交互**：[CP2] |

#### [N1] `Node ↻ Node`（自交互）
1. **N↔N Bus 中转**（Node 之间不直接调 on_message，所有交互经 Bus）
2. **N↔N Capability 声明**（每个 Node 在 NodeInfo.capabilities 暴露能力）
3. **N↔N 同 node_type 隔离**（同 type 不同 name 各自独立）

#### [N2] `Node → ActionMessage`
1. **1→N 接收 + 处理**（on_message 解码 msg_type → 路由到对应 ActionMessage handler）

#### [N3] `Node → Route`
1. **1→1 filter 订阅**（Node 自带 MessageFilter，描述感兴趣的 msg_type 集合）

#### [AM1] `ActionMessage → Node`
1. **N→1 trait 实现者**（所有 Node 都消费 ActionMessage）

#### [AM2] `ActionMessage ↻ ActionMessage`（自交互）
1. **1→1 msg_type 唯一 key**（每个 impl 的 `msg_type()` 返回字符串全局唯一）
2. **1→1 correlation_id 全局唯一**（response 匹配依据）
3. **1→1 intent 二元**（Query / Command）

#### [AM3] `ActionMessage → Route`
1. **1→1 按 msg_type 绑定 Route**（Engine / Bus 联合路由表）

#### [R1] `Route → Node`
1. **N→1 解析为目标 NodeId 列表**（Strict 直接 / Discovery 扫图）

#### [R2] `Route → ActionMessage`
1. **1→N 同 Route 可绑多个 ActionMessage 类型**（多个 msg_type 共用一条路由）

#### [R3] `Route ↻ Route`（自交互）
1. **二元变体**（Strict / Discovery，无 Any 模式）
2. **Capability AND-match**（requirements: Vec<(K, V)> 全部满足才匹配）
3. **单匹配**（顶层字符串字段；数组/嵌套不进 match）

#### [CR1] `CheckpointRule → Node`
1. **N↔N 不感知**（CheckpointRule 是 Engine 内部配置，不是 Node）

#### [CR2] `CheckpointRule → ActionMessage`
1. **1→1 build 返回 Box<dyn ActionMessage>**（CheckpointRule.build 产出消息）

#### [CR3] `CheckpointRule ↻ CheckpointRule`（自交互）
1. **N↔N 注册表**（Vec<CheckpointRule> 按 trigger 分组）
2. **N↔N 命名冲突**（name 不强制唯一，但建议唯一便于诊断）
3. **N↔N 闭包不可 Clone**（trait object，需 `Rc<CheckpointRule>` 包装才能 clone）

#### [CR4] `CheckpointRule → Checkpoint`
1. **1→1 trigger 字段**（每个 Rule 绑定一个 Checkpoint）

#### [CP1] `Checkpoint → CheckpointRule`
1. **1→N 反向触发**（每个 Checkpoint 位置遍历所有 trigger=该位置的 Rule）

#### [CP2] `Checkpoint ↻ Checkpoint`（自交互）
1. **5 个不变量位置**（编译期固定；不在范围内处理：Input 拦截 / system_prompt 组装 / Tool-Skill 发现 / TurnEnd 合并入 RoundEnd）

---

### §2.7 B ↔ C 跨组交互（5×5 = 25 格）

| ↓ 行 \\ → 列 | `ResourceSpec` | `ResourceRegistry` | `EngineBuilder` | `DiscoveryCache` | `NodeHandle` |
|---|---|---|---|---|---|
| **`Node`** | [N-RS] | [N-RR] | [N-EB] | [N-DC] | [N-NH] |
| **`ActionMessage`** | — | — | [AM-EB] | — | [AM-NH] |
| **`Route`** | — | [R-RR] | [R-EB] | [R-DC] | — |
| **`CheckpointRule`** | — | — | [CR-EB] | — | — |
| **`Checkpoint`** | — | — | [CP-EB] | — | — |

#### [N-RS] `Node → ResourceSpec`
1. **N→1 capabilities 自描述**（NodeInfo.capabilities 暴露，spec.capabilities 过滤后交集）

#### [N-RR] `Node → ResourceRegistry`
1. **N→1 build 期 snapshot**（build 时按 node_type + capabilities 匹配）

#### [N-EB] `Node → EngineBuilder`
1. **N→1 注册源**（EngineBuilder 通过 bus.snapshot() 收集所有 Node）

#### [N-DC] `Node → DiscoveryCache`
1. **N→1 缓存项**（cache[capability] = vec![this_node_id]）

#### [N-NH] `Node → NodeHandle`
1. **1→1 connect 返回**

#### [AM-EB] `ActionMessage → EngineBuilder`
1. **N↔N 处理器注册**（EngineBuilder 通过 ResponseProcessor 把 ActionMessage → Response）

#### [AM-NH] `ActionMessage → NodeHandle`
1. **1→1 publish 载荷**（NodeHandle.publish(msg) 内部把 ActionMessage 序列化为 Message）

#### [R-RR] `Route → ResourceRegistry`
1. **1→N 路由解析依赖**（Discovery 路由从 ResourceRegistry 查 NodeId）

#### [R-EB] `Route → EngineBuilder`
1. **1→N 持有**（EngineConfig.routes: HashMap<String, Route>）

#### [R-DC] `Route → DiscoveryCache`
1. **1→N Discovery Route 命中缓存**（Strict Route 不经缓存）

#### [CR-EB] `CheckpointRule → EngineBuilder`
1. **N→1 checkpoint_rule(rule) 注册**

#### [CP-EB] `Checkpoint → EngineBuilder`
1. **N→1 间接持有**（通过 CheckpointRule.trigger）

---

### §2.8 B ↔ D 跨组交互（5×4 = 20 格）

| ↓ 行 \\ → 列 | `Model` | `Tool` | `Skill` | `Hook` |
|---|---|---|---|---|
| **`Node`** | [N-D1] | [N-D2] | [N-D3] | [N-D4] |
| **`ActionMessage`** | [AM-D1] | [AM-D2] | [AM-D3] | [AM-D4] |
| **`Route`** | [R-D1] | [R-D2] | [R-D3] | [R-D4] |
| **`CheckpointRule`** | — | — | — | [CR-D1] |
| **`Checkpoint`** | — | — | — | [CP-D1] |

#### [N-D1] `Node → Model`
1. **N→1 ModelAdapter 即 Model Node**

#### [N-D2] `Node → Tool`
1. **N→1 MCP 节点内部 Tool 实现**

#### [N-D3] `Node → Skill`
1. **N→1 MCP 节点内部 Skill 实现**

#### [N-D4] `Node → Hook`
1. **N↔N 不直接实现**（Hook 通过 CheckpointRule 表达，不独立 Node）

#### [AM-D1] `ActionMessage → Model`
1. **1→1 ModelCall / ModelResponse**（标准消息）

#### [AM-D2] `ActionMessage → Tool`
1. **1→1 ToolCall / ToolExec / ToolResult**（标准消息）
2. **1→1 ToolCallSet / ToolResultSet**（DAG 批量消息）

#### [AM-D3] `ActionMessage → Skill`
1. **无标准 ActionMessage 类型**（Skill 加载通过 MCP `DiscoveryBackend` trait，非 msg_type；App 自定义 checkpoint rule 想扩展 Skill 行为时可定义新 msg_type）

#### [AM-D4] `ActionMessage → Hook`
1. **N↔N 无直接 ActionMessage**（Hook 通过 CheckpointRule 触发，非 msg）

#### [R-D1] `Route → Model`
1. **1→N model_call 路由到 ModelAdapter**（默认 Discovery(Capability::one("kind", "model"))）

#### [R-D2] `Route → Tool`
1. **1→N tool_exec 路由到 owning MCP**

#### [R-D3] `Route → Skill`
1. **1→N 走 MCP owning node**（Skill 加载不通过 msg_type，路由概念在此层不直接适用；Engine 通过 `ResourceRegistry` 按 MCP namespace 解析）

#### [R-D4] `Route → Hook`
1. **N↔N 无 Route 关联**（Hook 不走消息机制）

#### [CR-D1] `CheckpointRule → Hook`
1. **1→1 Hook 即 CheckpointRule 形态**（Hook 没有独立 Node 抽象）

#### [CP-D1] `Checkpoint → Hook`
1. **1→N 同 CheckpointRule.trigger**

---

### §2.9 B ↔ E 跨组交互（5×3 = 15 格）

| ↓ 行 \\ → 列 | `Session` | `Round` | `Turn` |
|---|---|---|---|
| **`Node`** | [N-E1] | [N-E2] | [N-E3] |
| **`ActionMessage`** | [AM-E1] | [AM-E2] | [AM-E3] |
| **`Route`** | — | — | — |
| **`CheckpointRule`** | [CR-E1] | [CR-E2] | [CR-E3] |
| **`Checkpoint`** | — | [CP-E2] | [CP-E3] |

#### [N-E1] `Node → Session`
1. **1→1 NodeId 含 session_id**（`engine/{session_id}`）

#### [N-E2] `Node → Round`
1. **1→1 round_id 注入 payload**（每轮 msg 带 round_idx）

#### [N-E3] `Node → Turn`
1. **1→1 turn_id 注入 payload**（每步 msg 带 turn_idx）

#### [AM-E1] `ActionMessage → Session`
1. **1→1 correlation_id 注入 session_id**（Engine 内部生成）

#### [AM-E2] `ActionMessage → Round`
1. **1→1 payload 含 round context**

#### [AM-E3] `ActionMessage → Turn`
1. **1→1 payload 含 turn context**

#### [CR-E1] `CheckpointRule → Session`
1. **1→N session 生命周期 hook**（通过 OverView 检查跨 session 状态）

#### [CR-E2] `CheckpointRule → Round`
1. **1→N round 边界 hook**（every_n_rounds 类型）

#### [CR-E3] `CheckpointRule → Turn`
1. **1→N turn 边界 hook**（合并入 RoundEnd + turn_count 判断）

#### [CP-E2] `Checkpoint → Round`
1. **1→1 RoundEnd 位置**

#### [CP-E3] `Checkpoint → Turn`
1. **1→1 合并入 RoundEnd**（无独立 TurnEnd checkpoint）

---

### §2.10 C 组内交互（5×5 = 25 格）

| ↓ 行 \\ → 列 | `ResourceSpec` | `ResourceRegistry` | `EngineBuilder` | `DiscoveryCache` | `NodeHandle` |
|---|---|---|---|---|---|
| **`ResourceSpec`** | **自交互**：[RS1] | — | [RS2] | — | — |
| **`ResourceRegistry`** | [RR1] | **自交互**：[RR2] | [RR3] | — | — |
| **`EngineBuilder`** | [EB1] | [EB2] | **自交互**：[EB3] | [EB4] | [EB5] |
| **`DiscoveryCache`** | — | — | [DC1] | **自交互**：[DC2] | — |
| **`NodeHandle`** | — | — | — | — | **自交互**：[NH1] |

#### [RS1] `ResourceSpec ↻ ResourceSpec`（自交互）
1. **N↔N name 唯一**（同 AgentConfig 内 name 不重复）
2. **1→1 node_type 分类**（mcp / mcp/pool / model / custom/xxx）
3. **1→1 capabilities 三态**（None / 显式子集 / `"all"` sentinel）

#### [RS2] `ResourceSpec → EngineBuilder`
1. **N→1 add_resource(spec) 注册**

#### [RR1] `ResourceRegistry → ResourceSpec`
1. **1→1 持有 build 期 snapshot**（HashMap<ResourceKey, NodeId>，key 含 spec name + node_type）

#### [RR2] `ResourceRegistry ↻ ResourceRegistry`（自交互）
1. **1→1 immutable after build**（运行时只读）
2. **N↔N 单 NodeId 原则**（同 ResourceSpec 只对应一个 NodeId，NodePool 内部 sub-bus 管理多 worker）

#### [RR3] `ResourceRegistry → EngineBuilder`
1. **1→1 build() 末尾构造并随 Engine 传递**

#### [EB1] `EngineBuilder → ResourceSpec`
1. **1→N 遍历所有 spec**

#### [EB2] `EngineBuilder → ResourceRegistry`
1. **1→1 构造 ResourceRegistry**

#### [EB3] `EngineBuilder ↻ EngineBuilder`（自交互）
1. **N→1 链式 API**（add_resource / checkpoint_rule / processor / on_member_failed 等）
2. **1→1 build() 一次性消费**

#### [EB4] `EngineBuilder → DiscoveryCache`
1. **1→1 构造 + 注入到 Engine**

#### [EB5] `EngineBuilder → NodeHandle`
1. **1→1 bus.connect() 获取后存入 Engine.handle**

#### [DC1] `DiscoveryCache → EngineBuilder`
1. **1→1 通过 Engine 间接持有**

#### [DC2] `DiscoveryCache ↻ DiscoveryCache`（自交互）
1. **1→1 TTL 失效**
2. **N↔N cache miss → 重算 from Bus.graph()**

#### [NH1] `NodeHandle ↻ NodeHandle`（自交互）
1. **N↔N 多 Bus 句柄**（NodeHandle 可关联多个 Bus，例 facade 节点）
2. **1→1 inbox mpsc + 多个 subscribe broadcast**

---

### §2.11 C ↔ D 跨组交互（5×4 = 20 格）

| ↓ 行 \\ → 列 | `Model` | `Tool` | `Skill` | `Hook` |
|---|---|---|---|---|
| **`ResourceSpec`** | [RS-D1] | [RS-D2] | [RS-D3] | [RS-D4] |
| **`ResourceRegistry`** | [RR-D1] | [RR-D2] | [RR-D3] | [RR-D4] |
| **`EngineBuilder`** | [EB-D1] | [EB-D2] | [EB-D3] | [EB-D4] |
| **`DiscoveryCache`** | [DC-D1] | [DC-D2] | [DC-D3] | [DC-D4] |
| **`NodeHandle`** | [NH-D1] | [NH-D2] | [NH-D3] | [NH-D4] |

#### [RS-D1] `ResourceSpec → Model`
1. **1→1 node_type="model"**

#### [RS-D2] `ResourceSpec → Tool`
1. **1→N node_type="mcp" 或 "mcp/pool"**

#### [RS-D3] `ResourceSpec → Skill`
1. **1→N node_type="mcp"（仅本地 MCP 含 Skill）**

#### [RS-D4] `ResourceSpec → Hook`
1. **N↔N 不直接**（Hook 不通过 ResourceSpec 声明）

#### [RR-D1] `ResourceRegistry → Model`
1. **1→1 target_of("model", name)**

#### [RR-D2] `ResourceRegistry → Tool`
1. **1→N tools_for_model() 聚合所有 MCP 注册的 ToolSpec**

#### [RR-D3] `ResourceRegistry → Skill`
1. **1→N skills_text() 聚合所有 MCP 注册的 Skill body**

#### [RR-D4] `ResourceRegistry → Hook`
1. **N↔N 不直接**

#### [EB-D1] `EngineBuilder → Model`
1. **1→1 校验 ModelDecl 节点在线**

#### [EB-D2] `EngineBuilder → Tool`
1. **1→N 校验所有 MCP 节点在线 + 无重名 tool**

#### [EB-D3] `EngineBuilder → Skill`
1. **1→N 校验所有本地 MCP 在线**

#### [EB-D4] `EngineBuilder → Hook`
1. **1→N 注册 checkpoint_rules**

#### [DC-D1] `DiscoveryCache → Model`
1. **1→1 cache[Capability("kind"="model")] → [adapter_node_id]**

#### [DC-D2] `DiscoveryCache → Tool`
1. **1→1 cache[Capability("kind"="mcp", "namespace"=ns)] → [mcp_node_id]**

#### [DC-D3] `DiscoveryCache → Skill`
1. **1→1 同 DC-D2**

#### [DC-D4] `DiscoveryCache → Hook`
1. **N↔N 不缓存**

#### [NH-D1] `NodeHandle → Model`
1. **1→1 publish model_call 句柄**

#### [NH-D2] `NodeHandle → Tool`
1. **1→1 publish tool_exec 句柄**

#### [NH-D3] `NodeHandle → Skill`
1. **N↔N 不直接相关**（Skill 加载通过 MCP `DiscoveryBackend` trait 方法，不走 publish）

#### [NH-D4] `NodeHandle → Hook`
1. **N↔N 不相关**

---

### §2.12 C ↔ E 跨组交互（5×3 = 15 格）

| ↓ 行 \\ → 列 | `Session` | `Round` | `Turn` |
|---|---|---|---|
| **`ResourceSpec`** | [RS-E1] | — | — |
| **`ResourceRegistry`** | [RR-E1] | — | — |
| **`EngineBuilder`** | [EB-E1] | — | — |
| **`DiscoveryCache`** | [DC-E1] | — | — |
| **`NodeHandle`** | [NH-E1] | [NH-E2] | [NH-E3] |

#### [RS-E1] `ResourceSpec → Session`
1. **1→N per-session ResourceSpec**（不同 session 可配置不同 resources）

#### [RR-E1] `ResourceRegistry → Session`
1. **1→1 per-session ResourceRegistry 实例**

#### [EB-E1] `EngineBuilder → Session`
1. **1→N per-session Engine 实例**

#### [DC-E1] `DiscoveryCache → Session`
1. **1→1 per-session DiscoveryCache 实例**

#### [NH-E1] `NodeHandle → Session`
1. **1→1 NodeId 含 session_id**

#### [NH-E2] `NodeHandle → Round`
1. **1→1 round 边界 msg 流**

#### [NH-E3] `NodeHandle → Turn`
1. **1→1 turn 边界 msg 流**

---

### §2.13 D 组内交互（4×4 = 16 格）

| ↓ 行 \\ → 列 | `Model` | `Tool` | `Skill` | `Hook` |
|---|---|---|---|---|
| **`Model`** | **自交互**：[MD1] | — | — | — |
| **`Tool`** | [TD1] | **自交互**：[TD2] | [TD3] | — |
| **`Skill`** | [SD1] | [SD2] | **自交互**：[SD3] | — |
| **`Hook`** | [HD1] | [HD2] | [HD3] | **自交互**：[HD4] |

#### [MD1] `Model ↻ Model`（自交互）
1. **N↔N 多 model 配置**（AgentConfig 主流单 model，但多 model 列表支持）
2. **p2p fallback**（第一个 online 失败时切换下一个）

#### [TD1] `Tool → Model`
1. **N→1 ToolSpec 注入 ModelCall.tools**（通过 Engine 间接）

#### [TD2] `Tool ↻ Tool`（自交互）
1. **N↔N name 唯一**（同 MCP namespace 内重名 → panic）
2. **N↔N DAG 依赖**（通过 `tool_call_set` 的 `ToolCallItem.blocked_by` / `blocking` 双向锁表达）
3. **p2p 子调用**（Tool 内部调其他 Tool）

#### [TD3] `Tool → Skill`
1. **N↔N 同 MCP 内共置**（通过 Skill 提供领域知识增强 Tool 行为）

#### [SD1] `Skill → Model`
1. **N→1 L2 注入 system_prompt**（含 Skill 描述 + L3 body）

#### [SD2] `Skill → Tool`
1. **N↔N Skill 描述可指引 Tool 选择**（语义层级）

#### [SD3] `Skill ↻ Skill`（自交互）
1. **N↔N name 唯一**（同 MCP namespace 内）
2. **N↔N L1/L2/L3 渐进披露**
3. **1→1 YAML frontmatter + Markdown body**

#### [HD1] `Hook → Model`
1. **N↔N 通过 CheckpointRule 关联**（BeforeModelCall / AfterModelCall 位置）

#### [HD2] `Hook → Tool`
1. **N↔N 同上**（BeforeToolExec / AfterToolExec）

#### [HD3] `Hook → Skill`
1. **N↔N RoundEnd 时刷新 Skill 列表**

#### [HD4] `Hook ↻ Hook`（自交互）
1. **N↔N CheckpointRule 列表**（Vec<CheckpointRule> 按 trigger 分组）

---

### §2.14 D ↔ E 跨组交互（4×3 = 12 格）

| ↓ 行 \\ → 列 | `Session` | `Round` | `Turn` |
|---|---|---|---|
| **`Model`** | [MD-E1] | [MD-E2] | [MD-E3] |
| **`Tool`** | [TD-E1] | [TD-E2] | [TD-E3] |
| **`Skill`** | [SD-E1] | [SD-E2] | [SD-E3] |
| **`Hook`** | [HD-E1] | [HD-E2] | [HD-E3] |

#### [MD-E1] `Model → Session`
1. **N→1 model_call response 注入 session 上下文**

#### [MD-E2] `Model → Round`
1. **N→1 每次 round 至少 1 次 model_call**

#### [MD-E3] `Model → Turn`
1. **1→1 每 turn 1 次 model_call**（可能带 tool_calls）

#### [TD-E1] `Tool → Session`
1. **N↔N Tool 结果进入 session history**

#### [TD-E2] `Tool → Round`
1. **N↔N Tool 调用在 round 内发生**

#### [TD-E3] `Tool → Turn`
1. **N↔N Tool 调用与 turn 1:1 或 1:N**

#### [SD-E1] `Skill → Session`
1. **N↔N Skill L2 全 session 注入 system prompt**

#### [SD-E2] `Skill → Round`
1. **N↔N Skill 内容在每 round 都可用**

#### [SD-E3] `Skill → Turn`
1. **N↔N 按需在 turn 内拉取 L3**

#### [HD-E1] `Hook → Session`
1. **N↔N session 边界 hook**（少见，多为 RoundEnd）

#### [HD-E2] `Hook → Round`
1. **1→1 RoundEnd 主战场**

#### [HD-E3] `Hook → Turn`
1. **N↔N 合并入 RoundEnd + turn_count 判断**

---

### §2.15 E 组内交互（3×3 = 9 格）

| ↓ 行 \\ → 列 | `Session` | `Round` | `Turn` |
|---|---|---|---|
| **`Session`** | **自交互**：[SS1] | [SS2] | [SS3] |
| **`Round`** | [RS4] | **自交互**：[RS5] | [RS6] |
| **`Turn`** | [TS1] | [TS2] | **自交互**：[TS3] |

#### [SS1] `Session ↻ Session`（自交互）
1. **1→1 session_id 全局唯一**
2. **1→1 per-session state_store**
3. **1→1 per-session trace 文件**（JSONL 格式）
4. **N↔N session 间 p2p**（队友 session）

#### [SS2] `Session → Round`
1. **1→N Round 序列**（session 内多轮对话）
2. **1→1 持久化时机**（每个 round 结束写 state_store）

#### [SS3] `Session → Turn`
1. **1→N 跨 round 的 turn 总数**

#### [RS4] `Round → Session`
1. **N→1 round 边界 session 持久化触发**

#### [RS5] `Round ↻ Round`（自交互）
1. **1→1 round_idx 单调递增**
2. **1→N turns 累积**

#### [RS6] `Round → Turn`
1. **1→N turns per round**（含 ReAct 步骤数）

#### [TS1] `Turn → Session`
1. **N→1 turn 边界更新 OverView**

#### [TS2] `Turn → Round`
1. **N→1 turn 属于一个 round**

#### [TS3] `Turn ↻ Turn`（自交互）
1. **1→1 turn_idx 单调递增**
2. **1→1 model_call + [tool_calls] 结构**
3. **1→1 最后一步可仅 model_call**

---

## §3 符号字典与关键交互模式

### 3.1 拓扑符号

| 符号 | 语义 | 出现频次 |
|------|------|---------|
| `p2p` | 点对点定向（`to: Vec<NodeId>` 非空） | 8 |
| `1→N` | 一对多广播（`to: Vec::new()` 或一次性派发多个 target） | 32 |
| `N→1` | 多对一响应（多个节点响应同一 Engine） | 22 |
| `N↔N` | 多对等多播 / 任意方向 | 28 |
| `1→1` | 单向独占 | 56 |
| `1→N subscribe` | 订阅式监听 | 9 |
| `1→1 implements` | trait 实现 | 12 |
| `1→1 解析为` | 声明 → 实例映射 | 8 |
| `1→1 注入` | 数据注入到构造体 | 7 |
| `1→1 持有` | 字段包含 | 31 |
| `1→1 读取` | 一次性读取 | 6 |
| `1→1 触发` | 触发器调用 | 7 |
| `1→1 构造` | 构造调用 | 12 |
| `—` | 无任何直接交互 | ~25 |

### 3.2 关键交互模式总结

1. **Engine ↔ Engine 自交互**（最丰富）：
   - p2p 队友私聊
   - 1→N 分配子任务
   - N→1 汇报
   - park/resume 生命周期

2. **Node → Bus 单向 push**（核心数据流）：
   - Node → Bus 通过 `NodeHandle::send()` 返回 `SendReceipt`
   - Bus → Node 通过 forwarding task 写入 inbound mpsc
   - NodeHandle 通过 `futures::select_all` 跨多订阅读取

3. **ModelAdapter → MCP 反向依赖**（唯一跨资源层耦合）：
   - `arf-model-adapter → arf-mcp` 单向 import
   - ModelAdapter 调 `tool_result_to_model_message(ToolResultItem) -> ModelMessage`

4. **Checkpoint 5 位置串行**（不可乱序）：
   - BeforeModelCall → publish model_call → AfterModelCall → BeforeToolExec → publish tool_exec → AfterToolExec → RoundEnd
   - max_turns 检查 3 处：model turn 前 / 后 / tool_exec 末尾

5. **ResourceSpec capabilities 双层语义**（待 reconcile）：
   - `arf-agent` 层：subset check + `None` 匹配任意节点
   - `phase7 design` 层：`DeclaredFilter::All | Subset | None_` 三态
   - 当前实现两套并存，`{"all": "sentinel"}` 是否真生效需 phase7 全部合入后验证

6. **State 单实例不可变引用**（生命周期约束）：
   - App 持有 State；Engine.run() 借 `&mut`
   - snapshot/restore 由 App 触发
   - **当前字段**：`messages + over_view + wait_events`（`tasks` 尚未实现）

7. **Route 二元变体**（无 Any 模式）：
   - Strict → 离线即 fail（`BuildError::MissingNodes`）
   - Discovery → 0 匹配即 warn；DiscoveryCache 缓存

8. **Response 单变体**（错误通过 ResponseProcessor 透传）：
   - `Done(Value)` 唯一形态
   - 业务错误 = payload 内字段

9. **ResponseProcessor 双层 dispatch**（重要）：
   - **白名单**：`"model_response"` / `"tool_result"` 走 Engine 内置 dispatch，无需 App 注册
   - **processors 表**：`AgentConfig.engine.processors: HashMap<String, Arc<dyn ResponseProcessor>>` 注册自定义 msg_type 的处理器
   - **命名约定**：App 自定义 msg_type 的响应 msg_type 预测为 `<msg_type>_result`（predict_response_msg_type）

10. **Bus 多实例 + drain_rx 机制**：
    - `Bus` 是 `tokio::sync::broadcast` + 永久 `drain_rx` 守护，发送无阻塞
    - 慢消费者通过 `Lagged(n)` 通知，不阻塞其他消费者
    - 多 Bus 场景：EngineBuilder 聚合 `bus.graph().nodes` 后 dedupe（同 NodeId 跨 Bus 行为未明确）

11. **AgentConfig 嵌套结构**：
    - `AgentConfig { model, resources, system_prompt_template, initial_memory, allowed_paths, engine: EngineConfig }`
    - `EngineConfig { routes, checkpoint_rules, processors, on_member_failed, max_turns=10, tool_timeout_ms=30_000 }`
    - 不 derive Clone/Debug/Serialize/Deserialize（因 Box<dyn Fn> / Arc<dyn ...>）

12. **ToolSpec 双类型**（命名陷阱）：
    - `arf_core::ToolSpec`：注入到 ModelCall.tools，3 字段（name + description + parameters）
    - `arf_agent::ToolSpec`：App 声明带权限，5 字段（含 permission + parameter_filter）
    - 命名空间不同，使用时需注意 `use` 来源

---

## §4 维护说明

### 4.1 文档更新时机

- 新增 phase → 新增 §1 卡片 + 补全 §2 相关子表
- 实体行为变更 → 更新 §1 卡片 + §2 对应单元格
- 新增交互模式 → 更新 §2 + §3.1 符号字典

### 4.2 与 phase 演进同步

| Phase | 新增实体 | 影响子表 |
|-------|---------|---------|
| Phase 1 (Bus) | Bus | §2.1-§2.15 所有涉及 Bus 的格子 |
| Phase 2 (State) | State | §2.1, §2.5 |
| Phase 3 (AgentConfig) | AgentConfig + ResourceSpec | §2.1, §2.3 |
| Phase 4 (ModelAdapter) | ModelAdapter | §2.1, §2.4 |
| Phase 5 (MCP) | MCP + Tool + Skill | §2.1, §2.4, §2.13 |
| Phase 6 (Engine) | Engine + Node + ActionMessage + Route + Checkpoint + CheckpointRule + DiscoveryCache + NodeHandle + ResponseProcessor + WaitEvent + Response | §2.1, §2.6, §2.7, §2.8, §2.10, §2.11, §2.12 |
| Phase 7 (ResourceRegistry) | ResourceRegistry + EngineBuilder | §2.3, §2.7, §2.10, §2.11 |

### 4.3 源码核对结论（2026-07-02）

> 本节基于对照 `crates/arf-core/`、`crates/arf-bus/`、`crates/arf-engine/`、`crates/arf-agent/` 源码的核对结果，标记文档中**与代码不一致**或**代码实现尚未到位**的部分。

#### 已对齐（代码实现与文档一致）

- [x] **CheckpointRule 4 元组** — `crates/arf-core/src/checkpoint.rs` 确认：name + trigger + when + build，**无 route 字段**，由 `AgentConfig.routes` 单一来源
- [x] **EngineBuilder.build() 流程** — `crates/arf-engine/src/builder.rs` 确认：聚合 graph → ResourceRegistry::build → 校验 Strict 路由 → 校验 CheckpointRule name 唯一 → Engine::new
- [x] **ResponseProcessor 分发表** — `crates/arf-engine/src/engine.rs:575` 确认：`self.config.engine.processors.get(msg.msg_type.as_str())`；`"model_response"` / `"tool_result"` 走 Engine 内置白名单，自定义 msg_type 走 processors 表
- [x] **DiscoveryCache 结构** — `crates/arf-engine/src/checkpoint.rs` 确认：`Mutex<HashMap<Vec<(String, String)>, Vec<NodeId>>>`，key 是 `Capability.requirements` clone
- [x] **DiscoveryCache 失效机制** — Engine::new 时 spawn lifecycle listener 监听 `node_online`/`node_offline` 触发 `invalidate()`
- [x] **WaitStrategy 默认** — `publish_and_await_query` 对 Query intent 默认 `WaitStrategy::All`（App 可覆盖）
- [x] **5 个 Checkpoint 位置** — `BeforeModelCall` / `AfterModelCall` / `BeforeToolExec` / `AfterToolExec` / `RoundEnd` 全部实现
- [x] **max_turns 终止检查** — model turn 前 + 后 + tool_exec 末尾共 3 处检查
- [x] **EngineConfig 默认值** — `max_turns=10`、`tool_timeout_ms=30_000`、`routes={}`、`checkpoint_rules=[]`、`processors={}`、`on_member_failed=None`
- [x] **Engine 内置响应映射** — `crates/arf-engine/src/engine.rs:625-632` `response_msg_type_for()`：model_call → model_response、tool_exec → tool_result、memory_op → memory_op_result、human_handoff → human_handoff_reply；其他 → `<msg_type>_result`
- [x] **MCP 节点 message_loop 过滤** — `crates/arf-mcp/src/node.rs` 确认：检查 `msg.is_for(&self.node_id) || msg.is_broadcast()` 而非 msg_type filter
- [x] **MCP 节点 dispatch 双入口** — `crates/arf-mcp/src/node.rs:132` 确认：`tool_call_set`（DAG）→ `tool_result_set`、`tool_exec`（单 tool）→ `tool_result`；后者按 `tool_name` 过滤，只 owner 响应
- [x] **MCP unified type** — 单一 `McpNode` 类型，构造差异通过 `DiscoveryBackend` (`FsDiscovery` / `HttpDiscovery`) + `RuntimeModule` (`LocalRuntime` / `RemoteRuntime`) 注入
- [x] **Skill 加载非 msg_type** — `crates/arf-mcp/src/discovery.rs:39-63` 确认：通过 `DiscoveryBackend` trait 方法 `resolve_skill` / `list_skills` / `load_skill_body` / `load_skill_resources` / `run_skill_tool`，不通过 Bus 消息
- [x] **ModelAdapter 单 msg_type filter** — `crates/arf-model-adapter/src/node.rs` 确认：`MessageFilter { types: Some(vec!["model_call"]), to_match: BroadcastAndDirectedToMe }`

#### 文档与代码不一致（需修正）

- [x] **AgentConfig 位置** — 文档原写 "in arf-engine"，实际在 `crates/arf-agent/src/config.rs`（`arf-engine` 仅含嵌套的 `EngineConfig`）— **已修正**
- [x] **ResourceSpec 字段名** — 文档原写 `name`，实际是 `resource_name`（带 `#[serde(alias = "name")]`）— **已修正**
- [x] **ToolSpec 双类型** — 文档仅提 `arf_core::ToolSpec`，忽略 `arf_agent::ToolSpec`（含 `permission` + `parameter_filter`）— **已补充双类型对照表**
- [x] **Heartbeat msg_type** — 文档原写 "heartbeat"，实际为 `"heartbeat_request"`— **已修正**
- [x] **State 字段** — 文档原写 "messages + tasks + over_view"，实际为 "messages + over_view + wait_events"（**v1.x design 中的 `tasks` 尚未实现**）— **已标注差异**
- [x] **EngineBuilder API** — 文档暗示链式 `add_resource()` / `checkpoint_rule()`，实际是 `EngineBuilder::new(buses).build(config)` 一次性 — **已修正**
- [x] **Bus 多实例** — 文档原假设单 Bus，EngineBuilder 实际接受 `Vec<Arc<Bus>>` 并聚合 graph — **已修正**
- [x] **DiscoveryCache TTL** — 文档原假设有 TTL，实际无 TTL；通过监听 graph 变更 invalidate — **已修正**
- [x] **Engine 缺省订阅语义** — `ToMatch::BroadcastAndDirectedToMe` — Engine 同时接收广播 + 定向到自身的 msg — **已隐含在 §B3 / §E1 单元格**
- [x] **NodeHandle API** — 文档原写 `publish / subscribe / disconnect / inbox`，实际是 `send / send_via / attach_to / recv / disconnect`（**send 不是 publish，recv 不是 inbox，attach_to 用于多 Bus 订阅**）— **已修正**
- [x] **EngineConfig 字段遗漏** — 漏掉 `processors` / `on_member_failed` / `tool_timeout_ms` 及默认值 — **已补充 §A6.1**
- [x] **OnMemberFailedHandler / MemberFailedAction** — 完全缺失 — **已补充 §A6.2 / §A6.3**
- [x] **ToolPermission** — 完全缺失 — **已补充 §C1.1**
- [x] **App 自定义 msg_type 响应命名约定** — 实际是 `<msg_type>_result`（predict 逻辑在 `publish_and_await_query`）— **已隐含于 §3.2**
- [x] **`tool_exec_set` msg_type 错误** — 文档原写 `tool_exec_set`，实际为 `tool_call_set`（DAG 批调用）— **已修正**
- [x] **`skill_request` / `skill_content` msg_type 错误** — 文档原假设存在，实际 Skill 加载通过 MCP `DiscoveryBackend` trait 方法，不走 msg_type — **已修正**
- [x] **MCP unified type** — 文档原假设 `LocalMcpNode` / `RemoteMcpNode` 是不同类型，实际是单一 `McpNode` 通过构造差异注入 `DiscoveryBackend` + `RuntimeModule` — **已在 §A3 卡片体现**

#### 代码实现缺失（待跟进）

- [ ] **`State.tasks` 字段** — v1.x design 提到 `tasks`（含双向锁 `blocked_by`/`blocking`），当前 `wait_events` 是简化替代。需要 phase2.2 / phase6 task 6.6 决策：是否要完整 tasks 还是继续用 wait_events
- [ ] **`ResourceSpec.capabilities` 三态语义统一** — `arf-agent` 定义的是 subset check，`phase7 resource-registry design` 引入 `DeclaredFilter::All|Subset|None_` 三态。当前实现两套语义并存，需要 reconcile（决定 `{"all": "sentinel"}` 是否真生效）
- [ ] **`Hook` 独立抽象** — 当前 Hook 通过 `CheckpointRule` 形态实现；如果未来需要 session 边界 hook，需要扩展 Checkpoint enum 或新建独立机制
- [ ] **多 Bus 场景下 Engine 的跨 Bus graph 聚合** — 当前 EngineBuilder 用 `merged.entry().or_insert()` 处理多 Bus 重复 NodeId，但重复 NodeId 的语义（reject? prefer primary?）未明确定义
- [ ] **slow consumer backpressure 语义** — `Lagged(n)` 只是通知丢失数量，App 层如何处理（重发？告警？）目前无统一约定
- [ ] **`ResponseProcessor` 内置白名单 msg_type 列表** — 当前知道 `"model_response"` 和 `"tool_result"` 走内置 dispatch，但 Engine 内是否有更完整的白名单需在 `engine.rs` 中通读确认
- [ ] **`AppConfig.engine.checkpoint_rules` 与 `AppConfig.engine.routes` 的关系** — 当前 `evaluate` 函数读 rules + routes + graph + cache 四者协同，App 写自定义 msg_type 时需要同时在 rules 和 routes 各加一项 — API 是否要打包成 "CheckpointSpec" 一体化对象？待决策

---

**End of document.**