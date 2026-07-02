# Phase 8 草案：抽象精炼 + Subagent/A2A 扩展点

> **Date**: 2026-07-02
> **Status**: Draft（基于 `docs/dev/entity-catalog.md` §4.3 + 当前实现现状）
> **目标**：把 v1.x 框架从"ReAct 单 agent"提升为"agent 运行时完整抽象"，具备 subagent 嵌套 + A2A 协调的扩展点
> **范围**：抽象精炼优先（5 个实体修订 / 5 个 ActionMessage 标准化），扩展点其次

---

## 0. 现状盘点（从哪里出发）

### 0.1 已完成的 Phase

| Phase | 主题 | 状态 |
|-------|------|------|
| 0 | 脚手架 | ✅ |
| 1 | Bus（多 Bus / broadcast / heartbeat / Discovery） | ✅ |
| 2 | State（messages + over_view + wait_events） | ✅ |
| 3 | AgentConfig（在 `arf-agent`，含 `subagents` / `teammates` 字段） | ✅ |
| 4 | ModelAdapter（4 provider） | ✅ |
| 5 | MCP（统一 McpNode + DiscoveryBackend + RuntimeModule） | ✅ |
| 6 | Engine（ReAct + 5 Checkpoint + ResourceRegistry） | ✅ 设计 |
| 7 | Resource Registry 设计稿（**未合并**） | 🔲 草案 |

### 0.2 已知缺口（来自 entity-catalog §4.3）

1. **State.tasks 字段缺失**（v1.x design 提到双向锁 `blocked_by`/`blocking`，当前用 `wait_events` 简化）
2. **AgentConfig 双设计并存**（`arf-agent` 的 `models: Vec`+`subagents`+`teammates` vs phase7 resource-registry 的单 `model`+`resources: Vec`）
3. **ActionMessage 仅 3 个**（ModelCall / ToolExec / ToolCall）；subagent / A2A / memory / human-handoff / streaming 都无 ActionMessage 定义
4. **Engine 不处理任何 subagent / A2A / memory / handoff 消息**（只 ReAct）
5. **Engine 不消费 streaming**（`model_response_chunk` 已发但 Engine 内部组装时仍按 full response 处理）
6. **Tool 执行串行**（engine.rs:192 注释 "sequential；6.6 加并发" — 未实现）
7. **Hook 仅 CheckpointRule 形态**（无独立抽象）
8. **慢消费者 backpressure 无约定**（`Lagged(n)` 只通知丢失数）
9. **多 Bus 同 NodeId 行为未定义**（EngineBuilder 用 `entry().or_insert()` 静默覆盖）
10. **`memory_op` / `human_handoff` 在 `response_msg_type_for` 表里有名字但无 ActionMessage impl**（属于"App 自定义"约定）

### 0.3 当前 entity 正交度评估

对照 entity-catalog §2 的 23×23 矩阵：

| 正交性 | 评估 |
|--------|------|
| **Bus ↔ Node ↔ Message** | ✓ 三者解耦清晰 |
| **Engine ↔ AgentConfig ↔ ResourceSpec ↔ ResourceRegistry** | ✓ 已实现 |
| **ActionMessage ↔ ResponseProcessor ↔ Route** | ✓ 但 ActionMessage 缺类型 |
| **Checkpoint ↔ CheckpointRule ↔ Engine** | ✓ 三位置分明 |
| **Engine ↔ Engine 自交互**（p2p / 1→N / N→1） | ⚠ 矩阵条目存在但无具体协议 |
| **Tool / Skill / Hook** 三原语 | ⚠ Tool/Skill 完整，Hook 仅 CheckpointRule 形态 |
| **Session / Round / Turn** | ✓ CLAUDE.md 约定 |
| **Subagent / A2A** | ✗ 完全空缺 |

---

## 1. 三大目标

### 1.1 抽象精炼（消歧义 + 补漏）

| 目标 | 落地动作 |
|------|---------|
| 消歧 AgentConfig 双设计 | 决策 + merge → 单一结构 |
| 补漏 ActionMessage 协议集 | 5 类标准消息（见 §3） |
| 提升 Hook 抽象 | CheckpointRule + 独立 SessionHook 抽象 |
| 补 State.tasks | 实现双向锁 tasks 字段 |

### 1.2 运行时完整性（覆盖 agent 全操作）

Engine 必须能驱动以下操作（不仅 ReAct）：

| 操作 | 当前 | 目标 |
|------|------|------|
| 单轮 ReAct（model + tool） | ✅ | ✅ |
| 流式 model 输出 | ⚠ 模型层有，Engine 不消费 | ✅ Engine 接受 chunk + 聚合 |
| 并行 tool 调用（DAG） | ❌ 串行 | ✅ DAG 并发 |
| Subagent 嵌套调用 | ❌ 无协议 | ✅ `delegate_task` ActionMessage |
| A2A peer 通信 | ❌ 无协议 | ✅ `peer_message` ActionMessage |
| Human handoff | ⚠ 表里有名字无 impl | ✅ `human_handoff` ActionMessage |
| Memory operation | ⚠ 表里有名字无 impl | ✅ `memory_op` ActionMessage |
| Context compaction | ⚠ 仅 CheckpointRule 钩子 | ✅ 内置 `when_context_over` CheckpointRule + 标准 Compactor Node |
| Streaming 中断 | ❌ 无 | ✅ pause/resume model_response 流 |

### 1.3 扩展到 subagents / A2A 的扩展点

框架应当提供：

- **Subagent 协议** — 父 Engine 调子 Engine 时，子 Engine 作为完整 Node 接入同 Bus，父通过 capability 匹配找到子
- **A2A 协议** — 多个 Engine 互为 peer；Engine 内置 peer_message / peer_reply 消息处理
- **Teammate 拓扑** — Star（主从）/ Mesh（对等）/ Chain（流水线）三种形态在 ResourceSpec 层声明
- **Session handoff** — session 状态可序列化 + 转移到另一 Engine

---

## 2. Phase 8 任务分解

### 2.1 任务总览

| ID | 任务 | 依赖 | 估时 | 优先级 |
|----|------|------|------|--------|
| 8.1 | AgentConfig 统一（决策 + merge） | — | 1 天 | P0 |
| 8.2 | ActionMessage 协议集扩展（5 类标准消息） | — | 3 天 | P0 |
| 8.3 | Engine 扩展：5 类消息 dispatcher + streaming 消费 | 8.2 | 4 天 | P0 |
| 8.4 | Engine 重构：State.tasks 双向锁 + 并发 tool_exec | — | 5 天 | P0 |
| 8.5 | Subagent 协议（嵌套 Engine 作为 Node） | 8.1, 8.3 | 4 天 | P1 |
| 8.6 | A2A 协议（peer_message / peer_reply / topology） | 8.3, 8.5 | 5 天 | P1 |
| 8.7 | MemoryOp / HumanHandoff ActionMessage 标准实现 | 8.2, 8.3 | 3 天 | P1 |
| 8.8 | Hook 抽象提升（SessionHook 独立 + CheckpointRule 仍存） | 8.3 | 2 天 | P2 |
| 8.9 | 内置 Compactor Node（context_utilization 触发） | 8.3, 8.8 | 2 天 | P2 |
| 8.10 | 多 Bus 重复 NodeId 语义定义 + 文档 | 8.1 | 1 天 | P2 |
| 8.11 | 慢消费者 backpressure 约定 + App 层指南 | — | 1 天 | P3 |
| 8.12 | entity-catalog.md 同步更新（28 实体 → 35+ 实体） | 8.1-8.7 | 1 天 | P0 |
| 8.13 | E2E 测试覆盖：subagent / A2A / streaming / 并发 tool | 8.4-8.6 | 4 天 | P0 |

**总估时：~36 天（约 7 周）**

---

## 3. 关键设计决策

### 3.1 AgentConfig 统一决策

**当前双设计：**

```rust
// A: arf-agent/src/config.rs（当前实现）
pub struct AgentConfig {
    pub system_prompt: String,
    pub models: Vec<ModelDecl>,           // 多 model
    pub tools: Vec<ToolSpec>,             // arf_agent::ToolSpec（带 permission）
    pub allowed_paths: Vec<String>,
    pub subagents: Vec<ResourceSpec>,     // 独立字段
    pub teammates: Vec<ResourceSpec>,      // 独立字段
}

// B: arf-engine/src/config.rs（phase7 草案，未合并）
pub struct AgentConfig {
    pub model: ModelDecl,                  // 单 model
    pub resources: Vec<ResourceSpec>,      // 统一 resources
    pub system_prompt_template: String,
    pub initial_memory: Vec<String>,
    pub allowed_paths: Vec<String>,
    pub engine: EngineConfig,
}
```

**推荐合并方案：**

```rust
// 目标（合并两者优点）
pub struct AgentConfig {
    /// 单模型主流 + fallback list 支持（A 方案的 models 字段改回 Vec<ModelDecl>）
    /// 但 Engine 选第一个在线的；fallback 在 phase6 OnMemberFailedHandler.SwitchTo 实现
    pub models: Vec<ModelDecl>,

    /// 统一资源声明（B 方案的 resources；subagents/teammates 合并进来）
    /// node_type 决定类别：
    ///   - "mcp" / "mcp/pool" → Tool / Skill
    ///   - "model" → 多 model 的额外声明（一般用 models 字段而非 resources）
    ///   - "agent/subagent" → 嵌套 Engine
    ///   - "agent/teammate" → A2A peer
    ///   - "memory" → Memory Node
    ///   - "custom/xxx" → App 自定义
    pub resources: Vec<ResourceSpec>,

    /// system_prompt 模板（含 {{memory}} / {{skills}} 占位符，由 Engine 替换）
    pub system_prompt_template: String,

    /// 初始 memory 列表（每条作为独立 system message 注入）
    pub initial_memory: Vec<String>,

    /// 沙箱允许的路径白名单
    pub allowed_paths: Vec<String>,

    /// 运行期配置
    pub engine: EngineConfig,
}
```

**决策点：**
- ❓ 保留多 `models: Vec<ModelDecl>` vs 收紧到单 `model`？**建议保留 Vec**（fallback 灵活）
- ❓ `subagents` / `teammates` 合并到 `resources`？**建议合并**（正交统一）
- ❓ `tools: Vec<ToolSpec>` 字段去留？**建议去**（tool 声明已在 MCP node 中 + Engine 通过 capabilities 过滤）

### 3.2 ActionMessage 协议集扩展

**当前：3 个**（ModelCall / ToolExec / ToolCall）

**目标：8 个标准 + N 个 App 自定义**

| # | ActionMessage | msg_type | 响应 | intent | 说明 |
|---|---------------|----------|------|--------|------|
| 1 | `ModelCall` | `model_call` | `model_response` / `model_response_chunk` | Query | 已实现，扩展 streaming |
| 2 | `ToolExec` | `tool_exec` | `tool_result` | Query | 已实现 |
| 3 | `ToolCallSet` | `tool_call_set` | `tool_result_set` | Query | 已实现（MCP 接收） |
| 4 | **`SubagentDelegate`** | `subagent_delegate` | `subagent_result` | Query | **新增**：父 → 子 Engine 派发任务 |
| 5 | **`PeerMessage`** | `peer_message` | `peer_reply` | Query | **新增**：Engine ↔ Engine p2p |
| 6 | **`PeerBroadcast`** | `peer_broadcast` | — | Command | **新增**：Engine 1→N 队友广播 |
| 7 | **`MemoryOp`** | `memory_op` | `memory_op_result` | Query | **新增**：调 Memory Node 存取 |
| 8 | **`HumanHandoff`** | `human_handoff` | `human_handoff_reply` | Query | **新增**：转人工 |

**9+：** App 可通过 `impl ActionMessage` 扩展。

### 3.3 Engine 重构（消息 dispatcher 化）

**当前 engine.run() 是 monolithic：** 主循环内 if/else 写死 model_call/tool_exec 两条路。

**目标：dispatcher 模式**

```rust
// 目标形态
pub struct Engine {
    config: AgentConfig,
    handle: NodeHandle,
    registry: ResourceRegistry,
    discovery_cache: Arc<DiscoveryCache>,

    /// 5 类内置消息的 handler（每个 handler 知道自己发什么 msg 等什么响应）
    handlers: HandlerRegistry,  // 新增
}

trait MessageHandler: Send + Sync {
    fn msg_type(&self) -> &'static str;
    fn handle(&self, engine: &mut Engine, msg: &Message) -> Result<HandlerOutcome, RunError>;
}

// 8 类消息 → 8 个 Handler impl
```

**收益：**
- 新增消息类型只加 handler，不动 Engine 主循环
- A2A / Subagent / Memory / Handoff 都是 handler 之一
- 与 checkpoint system 同样模式（位置不变 / 内容由订阅式填入）

### 3.4 Subagent 协议

**当前实现思路：** `subagents` 字段声明 + Engine 通过 Tool 间接调（实际无协议）

**目标实现：**

```rust
// SubagentDelegate
pub struct SubagentDelegate {
    pub correlation_id: Uuid,
    pub parent_session_id: String,
    pub subagent_node_id: NodeId,  // ResourceSpec 解析得来
    pub task: String,                // 子任务描述
    pub context: Value,              // 父 Engine 传给子的初始 context
}

impl ActionMessage for SubagentDelegate {
    fn msg_type(&self) -> &'static str { "subagent_delegate" }
    fn intent(&self) -> MessageIntent { MessageIntent::Query }
}

pub struct SubagentResult {
    pub correlation_id: Uuid,
    pub status: SubagentStatus,  // Success / Failed / Cancelled
    pub output: String,
    pub trajectory: Vec<ModelMessage>,  // 子的完整 ReAct 轨迹
    pub resource_usage: ResourceUsage,
}
```

**子 Engine 怎么启动？**
- 父 Engine 收到 subagent_delegate → 转发到子 Engine NodeId
- 子 Engine Node 是个标准 Engine 实例（独立 AgentConfig + State），通过 SubagentLauncher 启动
- 子 Engine 完成 → publish subagent_result → 父 Engine 接收

### 3.5 A2A 协议

**三种 topology：**

| 拓扑 | 适用 | ResourceSpec.node_type 示例 |
|------|------|---------------------------|
| **Star** | 主从式（orchestrator + workers） | 父 `agent/orchestrator`，子 `agent/worker` |
| **Mesh** | 对等协作（无主） | 所有 Engine 都声明 `agent/teammate` |
| **Chain** | 流水线（A → B → C） | 每段声明上下游 `agent/peer` + 阶段标识 |

**消息协议：**

```rust
// PeerMessage — 1:1 定向
pub struct PeerMessage {
    pub correlation_id: Uuid,
    pub from_session: String,
    pub to_session: String,
    pub content: String,
    pub attachments: Vec<Value>,
}

// PeerReply — 响应
pub struct PeerReply {
    pub correlation_id: Uuid,
    pub status: PeerStatus,
    pub content: String,
}

// PeerBroadcast — 1:N 广播
pub struct PeerBroadcast {
    pub correlation_id: Uuid,
    pub from_session: String,
    pub topic: String,
    pub content: String,
}
// 无响应（Command intent）
```

**Engine 处理 PeerMessage：**
1. 收到 peer_message → 调 LLM 决定怎么回（普通 chat）→ publish peer_reply
2. 收到 peer_broadcast → 异步消费，不回（Command intent）
3. App 可在 checkpoint rule 拦截 peer_message 做定制（如审计）

### 3.6 State.tasks 双向锁

**当前：`wait_events` 是简化替代**（只能配 WaitStrategy，不能表达 task DAG）

**目标：补 `tasks: Vec<Task>`**

```rust
pub struct Task {
    pub id: Uuid,
    pub status: TaskStatus,  // Created / InProgress / Blocked / Resolved / Failed / Cancelled
    pub blocked_by: Vec<TaskId>,  // 我等谁
    pub blocking: Vec<TaskId>,    // 谁等我
    pub payload: Value,
    pub created_at: Instant,
    pub updated_at: Instant,
}

pub enum TaskStatus {
    Created,
    InProgress,
    Blocked,    // wait_events 中
    Resolved,
    Failed,
    Cancelled,  // 沿 blocking 链级联
}
```

**迁移策略：**
- `wait_events` 保留作为 Engine 内部 WaitEvent 队列（轻量）
- `tasks` 是 App 视角的 task DAG（重语义）
- Engine 在 publish 时同时创建 WaitEvent 和 Task
- response 到达时更新两者

### 3.7 Hook 抽象提升

**当前：Hook = CheckpointRule**

**问题：**
- CheckpointRule 没有 session/round 边界的 hook（RoundEnd 是兜底）
- App 想做 session_start / session_end / round_start hook 无表达位置

**目标：新增 `SessionHook` trait**

```rust
pub trait SessionHook: Send + Sync {
    /// 唯一标识（用于日志 + 去重）
    fn name(&self) -> &str;

    /// 在 session 生命周期的哪个事件触发
    fn trigger(&self) -> SessionHookTrigger;
}

pub enum SessionHookTrigger {
    SessionStart,
    SessionEnd,
    RoundStart,
    RoundEnd,        // 与 Checkpoint::RoundEnd 等价但更明确
    TurnStart,
    TurnEnd,
    ErrorOccurred,
    NodeOnline,
    NodeOffline,
}

// EngineConfig 加一个 Vec<Arc<dyn SessionHook>>
```

**CheckpointRule vs SessionHook：**
- **CheckpointRule**：在 ReAct 主循环内 5 个位置（精确到 model_call / tool_exec 步骤）
- **SessionHook**：在 session/round/turn 边界（高层）
- 两者并存，不互相替代

---

## 4. 文档同步计划

### 4.1 entity-catalog.md 更新（任务 8.12）

新增实体（从 28 → 35+）：

| 新增 | 实体 | 所属组 |
|------|------|--------|
| 1 | `SubagentDelegate` / `SubagentResult` | B（ActionMessage 协议） |
| 2 | `PeerMessage` / `PeerReply` / `PeerBroadcast` | B |
| 3 | `MemoryOp` / `MemoryOpResult` | B |
| 4 | `HumanHandoff` / `HumanHandoffReply` | B |
| 5 | `ModelResponseChunk` | B |
| 6 | `Task` | A（State 子） |
| 7 | `TaskStatus` | A |
| 8 | `SessionHook` | B（trait） |
| 9 | `MessageHandler` | B（trait） |
| 10 | `HandlerRegistry` | C |

§2 矩阵扩展到 33×33 或更多。

### 4.2 新增 phase8 任务文档

```
docs/dev/phase8/
├── phase8-abstractions-design.md     # 整体设计
├── task-8.1-agent-config-merge.md
├── task-8.2-action-message-protocol.md
├── task-8.3-engine-dispatcher.md
├── task-8.4-state-tasks-and-parallel.md
├── task-8.5-subagent-protocol.md
├── task-8.6-a2a-protocol.md
├── task-8.7-memory-handoff.md
├── task-8.8-session-hook.md
├── task-8.9-compactor-node.md
├── task-8.10-multi-bus-semantics.md
├── task-8.11-backpressure-convention.md
├── task-8.12-entity-catalog-sync.md
└── task-8.13-e2e-tests.md
```

### 4.3 roadmap.md 更新

在 `docs/dev/2026-06-26-arfv1-roadmap.md` 增加 Phase 8 段落，并标记 Phase 7 状态为"设计完成 / 部分合并"。

---

## 5. 风险与权衡

### 5.1 主要风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| **ActionMessage 集过度膨胀** | Engine handler 表臃肿，新人上手难 | 8 类标准 + 强调 App 可扩展；文档清晰分级 |
| **AgentConfig merge 破坏向后兼容** | 现有用户配置文件失败 | `#[serde(alias)]` + 版本号字段 + deprecation warning |
| **Subagent 嵌套深度失控** | 栈溢出 / 资源耗尽 | EngineConfig.max_subagent_depth 限制 + 资源预算 |
| **A2A peer 拓扑枚举爆炸** | App 难以选择 | 三种基础 topology + 文档示例，不强制 |
| **State.tasks 与 wait_events 双轨** | 维护成本 | 长期目标合并到 tasks；短期共存 |

### 5.2 决策点

需要在 Phase 8 启动前决定的：

- **D1**：AgentConfig 是 `models: Vec` 还是单 `model`？（**默认建议 Vec**）
- **D2**：subagents/teammates 合并到 resources 还是保留独立字段？（**默认建议合并**）
- **D3**：Subagent 启动是父 Engine 内嵌子 Engine 还是独立进程？（**默认建议独立进程 + 子 Node，便于扩展**）
- **D4**：Peer message 是否走 Engine 自身 MessageFilter？（**默认建议是**）
- **D5**：Memory 是否内置 Node？（**默认建议内置 + 可禁用**）

---

## 6. 验收标准

### 6.1 完成定义

- [ ] AgentConfig 单一形态（8.1）
- [ ] 8 类标准 ActionMessage 全部实现（8.2）
- [ ] Engine dispatcher 化（8.3）— 新增消息类型无需改主循环
- [ ] State.tasks 字段 + 并发 tool_exec（8.4）
- [ ] Subagent e2e 通过（8.5 + 8.13）
- [ ] A2A 三种 topology e2e 通过（8.6 + 8.13）
- [ ] entity-catalog.md 同步（8.12）
- [ ] docs/api/ 增加 subagents.md + a2a.md + streaming.md 用户文档
- [ ] 所有 Rust + Python 测试通过（`make test`）
- [ ] 无 lint warning（`make lint`）

### 6.2 关键 demo

```python
# Subagent demo
parent = AgentConfig(...).add_subagent("researcher", capabilities={"skills": ["web_search"]})
parent.run("Find the latest news about ARF and summarize")
# → 自动 spawn 子 Engine → 子跑 web_search → 返回 summary

# A2A demo
peer_a = AgentConfig(..., topology="mesh").add_teammate("peer_b")
peer_b = AgentConfig(..., topology="mesh").add_teammate("peer_a")
# → 两 Engine 互发 peer_message → 协调任务

# Streaming demo
agent = AgentConfig(..., stream=True)
async for chunk in agent.stream("Write a poem"):
    print(chunk.content)
```

---

## 7. 启动建议

**第 1 周（紧迫）：**
- D1-D5 决策敲定（一次性会议）
- 启动 8.1（AgentConfig merge）
- 启动 8.2（ActionMessage 协议集设计稿）

**第 2-3 周（核心）：**
- 8.3 Engine dispatcher 重构（最大单点）
- 8.4 State.tasks + 并发

**第 4-6 周（扩展）：**
- 8.5/8.6 Subagent + A2A（依赖 dispatcher）
- 8.7/8.8/8.9 Memory/Handoff/Hook/Compactor

**第 7 周（收尾）：**
- 8.10/8.11 多 Bus + backpressure
- 8.12 entity-catalog 同步
- 8.13 E2E 测试 + 文档

---

## 8. 待讨论（不在 Phase 8 内）

- [ ] 是否引入**显式 Memory Node**作为框架组件？还是仅作为 App 通过 ActionMessage 扩展？
- [ ] **Sandbox 抽象**：当前 MCP 内置 LocalRuntime / RemoteRuntime，但 Engine 直接执行 tool 无沙箱。要不要 Engine 也引入 sandbox 边界？
- [ ] **持久化 schema 演进**：AgentConfig / State 的 serde 兼容策略（是否需要 semver？）
- [ ] **observability**：Bus 上一切天然可 trace，但要否引入 OpenTelemetry / Prometheus 标准？
- [ ] **跨语言**：除 Python 绑定外，是否需要 TypeScript / Go 绑定？subagent 协议在异构语言间如何对齐？

---

**End of draft.** 需用户对决策点 D1-D5 + 任务优先级给出指示，再启动 phase8 任务文档逐个编写。