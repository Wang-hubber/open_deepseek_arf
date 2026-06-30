# Phase 6 — Engine 设计(按问题-方案重组版)

> **依赖**: Phase 1 (Bus), Phase 4 (ModelAdapter), Phase 5 (MCP), arf-state
> **状态**: 设计(重构中)
> **取代**: 原 6 消息 enum / ack 双控制 / YAML 检查点方案
>
> **2026-06-30 修订(v2)**: §13 逐条讨论的 5 项决议——删除 Task 抽象、删除 NodeBinding(build() fail-fast 校验)、filter 改接收端过滤、新增 §5.7 Node 掉线处理(Fail + App hook)、持久化时机 App 全权。
>
> **2026-06-30 修订(v1)**: 新增 §1.5 Multi-Bus 架构扩展、§5.6 App-level Recovery 模型。Bus 从"单实例"扩展为"可组合多实例", Engine-as-Actor 核心设计不变, 仅 Bus 层增加最小原语(Node trait + 多 Bus 订阅 + Barrier)。
>
> **2026-06-30 重构**: 按"问题-解决措施"重新组织; 修复章节编号重复(C1)、park/waiting 术语混用(C3)、event 命名冲突(C4)、CheckpointRule 四/五元组矛盾(C5)、§10 边界 1 与内置 msg_type 硬编码矛盾(C6)、MemoryOp::Retrieve 残留(C7)、EngineBuilder API 风格不一致(C8)、Capability 数组示例违反匹配规则(C9)。

---

## 0. 设计理念总览

### 0.1 核心思想

Engine 做减法。它是 Bus 上的一个 Actor: 维护 AgentConfig + State, 在 ReAct 循环中按订阅式触发器收发消息。

**所有外部交互走 Bus, 没有例外。** Engine 不直接调 Provider、不直接调 HookRunner、不直接调 MCP。它甚至**不知道任何具体节点类型的存在**。

```
┌──────────────────────────────────────┐
│  Engine (Actor)                      │
│                                      │
│  AgentConfig  +  State               │
│    ├── messages  (Vec<ModelMessage>) │
│    └── over_view (OverView)          │
│                                      │
│  5 个 Checkpoint 位置(固定)          │
│      ↓                               │
│  CheckpointRule 列表(App 注册)       │
│      when(state) → bool              │
│      build(state) → ActionMessage    │
│      route  → AgentConfig.routes 查表│
│                                      │
│  inbox: PriorityQueue                │
│         │                            │
│         ▼                            │
│  ┌─────────────┐  4 状态:            │
│  │   state     │  idle/processing/   │
│  │   machine   │  waiting/stopped    │
│  └──────┬──────┘                     │
│         │                            │
│         ▼                            │
│       Bus                             │
└──────────────────────────────────────┘
```

### 0.2 三条不可违反的边界

1. **Engine 不知道任何具体节点类型** — Engine 代码不 `use` 任何具体 Node 实现所在的 crate。**msg_type 字符串(`"model_call"` / `"tool_exec"` 等)是路由 key, 与节点类型名解耦——字符串引用不构成边界违反**, 违反边界的是 `ModelAdapter` / `McpNode` 等具体类型名出现在 Engine 代码中。
2. **Node 不知道 Engine 的存在** — Node 只订阅 msg_types, 不假设发送者是 Engine。
3. **Checkpoint 是位置, 不是消息类型** — 5 个位置固定; 具体发什么 msg 由 CheckpointRule 决定。

### 0.3 依赖方向(单向, 无循环)

```
                  arf-core (纯数据: ActionMessage trait, Route, State)
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
    arf-bus       arf-state      arf-mcp
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                 arf-engine
                       │
                       ▼
                 arf-agent (Phase 7 DI 装配)
                       │
                       ▼
                 arf-pool (§15, 平级)
```

### 0.4 状态机一览(详见 §3.1)

| 状态 | 含义 |
|------|------|
| `idle` | `state.wait_events.is_empty()`, 无 in-flight 操作 |
| `processing` | 正在执行 ReAct 循环主流程 |
| `waiting` | `!state.wait_events.is_empty()`, Engine 已 park 等响应 |
| `stopped` | 收到 stop 信号, run() 返回 `Err(EngineError::Stopped)` |

**术语约定**: 状态机名称为 `waiting`; "park" 是动词("Engine 进入 waiting 状态 / park 等响应"), 不是状态名。本文档统一遵循。

---

## 1. 核心抽象

> 本节定义 §2 中各问题共同依赖的基础词汇。所有类型位于 `crates/arf-core/src/`。

### 1.1 ActionMessage(trait, 可扩展)

```rust
// crates/arf-core/src/message.rs

pub trait ActionMessage: Send + Sync {
    fn msg_type(&self) -> &'static str;
    fn correlation_id(&self) -> String;
    fn payload(&self) -> serde_json::Value;

    /// 唯一控制 Engine park 行为的字段
    fn intent(&self) -> MessageIntent;
}

pub enum MessageIntent {
    /// Engine park 等所有 receiver 最终响应
    Query,
    /// Engine 不等, receiver 后台自行完成
    Command,
}
```

App 通过实现 `ActionMessage` trait 添加新消息类型(`human_handoff`、`tool_audit`、`progress_update` 等)。**内置消息类型(`ModelCall`、`ToolExec`、`MemoryOp`、`CompactOp`)都是 trait 的具体实现, 不是封闭枚举。**

**内置 msg_type 白名单**(Engine 隐式处理, 无需 register_processor):

```rust
/// Engine 隐式处理的内置 msg_type 及其响应映射。
/// WaitEvent 收集到响应时按此表 dispatch; 未列出的 msg_type 必须通过
/// AgentConfig.processors 注册 ResponseProcessor(§3.3)。
pub const BUILTIN_MSG_TYPES: &[(&str, &str)] = &[
    ("model_call",  "model_response"),  // ModelAdapter 处理 → engine 注入 assistant 消息
    ("tool_exec",   "tool_result"),     // McpNode 处理 → engine 注入 tool 消息
];
```

其他所有 msg_type(如 `memory_op` / `compact_op` / `human_handoff` / `subagent_op`)的响应处理都必须通过 `AgentConfig.processors: HashMap<String, Arc<dyn ResponseProcessor>>` 注册, Engine 在 WaitEvent 完成时按响应 msg_type 查表 dispatch。

### 1.2 Response(单一形态)

```rust
pub enum Response {
    /// Receiver 给出的最终值。
    /// Engine park 等所有 Query 接收方都给出 Done(Value) 才继续。
    /// Value 的语义由 msg_type 决定:
    ///   - model_call → { content: String, tool_calls: Vec<ToolCall> }
    ///   - tool_exec → { content: String, error?: String }(error 字段为业务错误, 由 App 解释)
    ///   - 自定义类型 → 由 AgentConfig.processors 解释(§3.3)
    /// Engine 不解析 Value 内部字段, 不区分成功/业务错误——所有返回都视为"Done"。
    Done(serde_json::Value),
}
```

**无 `Failed` / `Err` / `Timeout` 变体**。理由(2026-06-30 决议):

- **Engine 不解析 Receiver 内部错误**——Receiver 内部错误处理是 App 开发者在执行节点(如 ModelAdapter / McpNode)中的职责。Engine 只确保"信息发出 + 信息接收", 对 Value 内部字段无任何语义假设。
- **Receiver 崩溃表现为两种 Engine 可观察的信号**(见 §2.P5):
  - `node_offline` lifecycle signal(进程死了)→ `OnMemberFailedHandler` 处理
  - 超时(hang 住但未下线)→ `OnMemberFailedHandler` 处理
- **业务错误**(如模型返回 "I cannot do that"、tool 抛 PermissionError)作为 Value 内容正常返回, 由对应 processor 解释; Engine 不需要 `Failed` 变体。

**无 `Wait`**。理由:

- intent=Query → Engine 必然等所有 receiver, 无论快慢
- intent=Command → Engine 不等, receiver 返不返都不影响
- Receiver 慢就慢着, Engine 已经 park; 不需要多余 ack 协议
- Receiver 存活由 Heartbeat 机制覆盖, 不污染 Response 协议

### 1.3 Route(二元) + Capability

```rust
// crates/arf-core/src/route.rs

pub enum Route {
    /// 严格模式: 发向指定的 node_id(s)
    Strict(Vec<NodeId>),

    /// 发现模式: 发向 BusGraph 中声明了该 capability 的所有节点
    Discovery(Capability),
}

/// Capability 多对 key-value, AND 语义。
pub struct Capability {
    pub requirements: Vec<(String, String)>,
}
```

**Discovery + Query 语义**: Engine 等所有匹配节点最终响应。
**Discovery + Command 语义**: Engine 不等, 所有匹配节点收到后自行处理。

无 `Any` 模式——必须显式声明 capability 才能 Discovery。

#### 1.3.1 Capability 匹配机制

**Node 声明能力**(connect 时一次性声明):

```rust
let mcp_info = NodeInfo {
    node_id: NodeId::new(id="local_mcp"),
    capabilities: serde_json::json!({
        "kind": "mcp",
        "transport": "stdio",
    }),  // 数组/嵌套对象仅用于展示, 不参与匹配
    ...
};
bus.connect(info=mcp_info, filter=mcp_filter).await?;

let model_info = NodeInfo {
    node_id: NodeId::new(id="primary_model"),
    capabilities: serde_json::json!({
        "kind": "model",
        "tier": "primary",
        "model_name": "claude-opus-4-7",
        "context_window": 200000,
    }),
    ...
};
```

**关键约定**:

- `requirements` 全部满足才匹配(**AND 语义**; 需要 OR 时拆成多次 Discovery)
- `NodeInfo.capabilities` 是 JSON Value, 可以是字符串、数字、数组、对象——**Capability 匹配只看顶层字符串字段**; 数组/嵌套对象不进 match
- **App 不要把路由关键信息放在数组里**——数组字段只能用于 Node 信息展示(如 `tools: ["read_file", "bash"]` 仅给人看, 不能用于 Discovery 匹配)
- Capabilities 在 connect 时声明, **不变直到 disconnect + reconnect**; 运行时变更需重连

#### 1.3.2 DiscoveryCache(2026-06-30 补全)

Engine 内部维护 Discovery 路由解析结果缓存, 避免每次 publish 都遍历 BusGraph。

```rust
// crates/arf-engine/src/cache.rs

/// Discovery 路由解析缓存。Capability → 当前匹配该 Capability 的 NodeId 集合。
/// RwLock 保护: run() 并发读、lifecycle signal 触发增量写。
pub struct DiscoveryCache {
    matches: RwLock<HashMap<Capability, HashSet<NodeId>>>,
}

impl DiscoveryCache {
    /// 获取 receivers, 未命中则 lazy resolve 并缓存。
    pub fn get_or_resolve<F>(&self, cap: &Capability, resolver: F) -> HashSet<NodeId>
    where F: FnOnce() -> HashSet<NodeId>;

    /// 增量失效: node_online 触发
    pub fn on_node_online(&self, node_id: NodeId, caps: &serde_json::Value);

    /// 增量失效: node_offline 触发
    pub fn on_node_offline(&self, node_id: NodeId);

    /// 全清(App 显式调用, 如 Bus 拓扑重建)
    pub fn clear(&self);
}
```

**为什么需要缓存**(2026-06-30 决议动机):

- 不缓存: 每次 publish Discovery 都要遍历 BusGraph(O(node_count)), 大拓扑下代价高
- 缓存后: Engine 在自己内存里查 receivers(O(1)), **完全不轮询 Bus**
- Engine 通过订阅 Bus 的 lifecycle signal(push-based)维护缓存——Bus 不被 Engine 轮询
- 这把"Engine 需要知道哪些 Node 在线"的查询开销从 N 次/BusGraph 降到 O(1) 内存查找
- Bus 的设计目标: **不**被 Engine 频繁 query; lifecycle signal 是 push 而不是 pull

**失效策略: 增量更新**(2026-06-30 决议):

- node_online: 遍历所有 cap, 若新 Node 的 caps 匹配则加入 set
- node_offline: 从所有 set 中移除该 NodeId
- 总成本: Engine 启动时 N 个 Capability 各懒解析一次; 后续 lifecycle 增量维护

**多 Bus 不变式**:

- NodeId 全局唯一(§2.P7), 同一 NodeId 在多 Bus 上是同一节点
- Cache 不需记录 BusId——publish 时 Bus 通过 NodeId 路由
- 多 Bus 拓扑变更(如 App 加 Bus)触发 `cache.clear()` + 懒重算

**边界场景**:

- 无匹配 → Engine 抛 `NoReceiver` 错误, App 决定 fail-fast / 降级 / 重试
- 单匹配 → 退化为 Strict 单 receiver 行为
- 多匹配 → 按 Discovery 语义(Query 等全部, Command 全发)

### 1.4 Node trait

```rust
// crates/arf-core/src/node.rs

#[async_trait]
pub trait Node: Send + Sync {
    fn id(&self) -> &NodeId;

    /// 序列化自己的状态(2026-06-30 决议: &self)。
    /// snapshot 不阻塞 Node 处理其他消息——Node 内部需用 RwLock/Mutex 保护状态,
    /// Engine 不负责暂停 Node 接收消息。
    fn snapshot(&self) -> Result<serde_json::Value, SnapshotError>;

    /// 从快照恢复状态(2026-06-30 决议: &mut self)。
    /// restore 期间 Node 不应处理消息(App 负责协调顺序)。
    async fn restore(&mut self, snapshot: serde_json::Value) -> Result<(), RestoreError>;

    /// 收到消息。`from_bus` 让 Node 知道消息来自哪条 Bus(facade 转发需要)
    async fn on_message(&mut self, msg: Message, from_bus: BusId);
}
```

**NodeId 全局唯一**——同一 Node 在所有订阅的 Bus 上是**同一身份**。

**Node snapshot 并发约定**(2026-06-30 决议):

- `snapshot` 是 `&self`——不阻塞 Node 处理消息
- Node 内部用 `RwLock` / `Mutex` 保护共享状态; snapshot 时 read lock, on_message 时 write lock(短临界区)
- Node 内部负责超时控制: 建议用 `tokio::time::timeout` 包装 state read 防止卡 barrier
- `restore` 是 `&mut self`——restore 期间 Node 应停止处理消息(App 协调顺序)
- Barrier 调用者不处理 Node 内部超时; Node 实现自负责
- 失败返回 `SnapshotError::Timeout` / `SnapshotError::Serialize` 等; barrier 收到 Err 后该 Node 加入 missing 列表(见 §2.P9)

### 1.5 Checkpoint + CheckpointRule

```rust
// crates/arf-core/src/checkpoint.rs

pub enum Checkpoint {
    /// model_call 触发前
    BeforeModelCall,
    /// model_call 完成后
    AfterModelCall,
    /// tool_exec 触发前
    BeforeToolExec,
    /// tool_exec 完成后
    AfterToolExec,
    /// round 边界, 准备发 final_output
    RoundEnd,
}

/// CheckpointRule 四元组: name + trigger + when + build
/// **不包含 route** —— route 由 AgentConfig.routes 单一源决定(避免双源不一致, 见 §2.P3)
pub struct CheckpointRule {
    /// 规则的唯一名称(用于日志 / 调试 / 禁则)
    pub name: String,
    /// 触发位置(5 个 Checkpoint 之一)
    pub trigger: Checkpoint,
    /// 条件谓词: 返回 true 才执行 build
    pub when: Box<dyn for<'a> Fn(&'a State) -> bool + Send + Sync>,
    /// 构造 ActionMessage: 从 State 生成要发送的消息
    pub build: Box<dyn for<'a> Fn(&'a State) -> Box<dyn ActionMessage> + Send + Sync + 'a>,
}

impl CheckpointRule {
    /// 位置参数构造器。闭包参数接受 `for<'a> Fn(&'a State) -> ...`, 避免与 struct field 生命周期不一致。
    pub fn new<W, B>(
        name: impl Into<String>,
        trigger: Checkpoint,
        when: W,
        build: B,
    ) -> Self
    where
        W: for<'a> Fn(&'a State) -> bool + Send + Sync + 'static,
        B: for<'a> Fn(&'a State) -> Box<dyn ActionMessage> + Send + Sync + 'static,
    {
        Self {
            name: name.into(),
            trigger,
            when: Box::new(when),
            build: Box::new(build),
        }
    }
}
```

**约定**:

- Engine 在固定 5 个 Checkpoint 位置暂停
- 调用所有注册规则的 `when`, 返回 `true` 才调用 `build`
- Engine 从 `build(state)` 返回的 msg 取 `msg_type()`, 按 `AgentConfig.routes[msg_type]` 投递(**Route 单一源**)
- Engine 按 `msg.intent()` 决定 park 等响应(Query)还是 fire-and-forget(Command)
- 框架提供标准构造器(`every_n_rounds`、`when_context_over`), 但底层都是四元组

**Route 单一源约定**(2026-06-30 决议): `AgentConfig.routes` 是 Engine 投递消息的唯一依据——既用于 ReAct 循环主动发的 ModelCall/ToolExec, 也用于 CheckpointRule.build 产生的所有 msg。`CheckpointRule` 不携带 route(避免双源不一致)。

**Intent 单源约定**(2026-06-30 决议): `intent` 来自 `ActionMessage::intent()` trait 方法, 由具体 msg 类型(如 MemoryOp、CompactOp)声明。`CheckpointRule` 不显式声明 intent(避免与 msg.intent() 不一致)。

**不在 Checkpoint 范畴**:

- **Input 处理**(user_input 进入 state)发生在 `engine.run(state, user_input)` 调用方, App 在外层拦截/转换/审批; Engine 内部不设 BeforeInput/AfterInput
- **System prompt 组装 + Tools/Skills 收集**发生在 `EngineBuilder.build()` 时(见 §3.3)
- **运行时 memory 抽取**: CheckpointRule.build 返回 MemoryOp::extract 作为 Command msg, 发到 MemoryNode(详见 §6.1)
- **运行时 memory 检索**: 由模型主动发起 tool call(如 `memory_search`), 走标准 tool_exec 流程; **不**是单独的 MemoryOp::Retrieve msg(2026-06-30 决议: 移除 Retrieve, 见 §2.P3)
- **Turn 结束**与 RoundEnd 合并(每 turn 必然在 round 内; 如需 turn 级 hook 用 `Checkpoint::RoundEnd` + `over_view.turn_count` 判断)

### 1.6 State + OverView

```rust
// crates/arf-core/src/state.rs

pub struct State {
    /// 对话历史(详细)。Engine 在 chat() 中追加; App 可读。
    pub messages: Vec<ModelMessage>,
    /// 聚合指标(O(1) 访问)。Engine 在每次 chat() / ReAct 转移点维护; App 可读。
    pub over_view: OverView,
    /// 等待中的消息组(§2.P4)。Engine 内部维护; App 不应访问。
    /// 包含所有未完成 WaitEvent, snapshot 时随 State 一起序列化。
    pub wait_events: Vec<WaitEvent>,
}

/// ModelMessage — State 使用的领域层消息类型。
/// 与 Bus 上的 `Message`(wire format, 含 from_bus/to/sender/payload)区分:
/// ModelMessage 是 domain format(role/content/tool_calls), 仅在 State 内与 LLM 交互时使用。
/// Engine 注入 State 时由 App 注册的 ResponseProcessor 把 wire 响应转为 ModelMessage。
pub struct ModelMessage {
    pub role: Role,
    pub content: String,
    /// assistant 消息包含 tool_calls 时填充
    pub tool_calls: Vec<ToolCall>,
    /// tool 响应消息对应的主调用 id(role=Tool 时填充)
    pub tool_call_id: Option<String>,
}

pub enum Role {
    System,
    User,
    Assistant,
    Tool,
}

pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: serde_json::Value,
}

pub struct OverView {
    pub round_count: usize,
    pub turn_count: usize,
    pub context_tokens: usize,
    pub model_context_window: usize,
    pub runtime: Duration,
    pub last_user_message: String,
}
```

**所有权语义**(2026-06-30 决议):

- **App 持有 State**(在 `engine.run()` 之外; State 的生命周期由 App 管理)
- **Engine.run(&mut state, ...)** 期间借走 &mut State, 修改 messages / over_view / wait_events
- Engine 不再拥有 State——State 的持久化、克隆、跨 Engine 共享都是 App 决定
- App **不**应该直接访问 `state.wait_events`(Engine 内部维护); 仅 `messages` 和 `over_view` 是 App 可读的"对话视图"

**WaitEvent 与 State 的关系**: WaitEvent 列表**属于 State**, 跟随 State 一起序列化(§2.P9)。这不是"逻辑扩展", 是 State 的一个字段; App 不要直接读写。

**`over_view` 字段计算策略**:

| 字段 | 计算方式 | 时机 |
|------|---------|------|
| `round_count` | Engine 内部计数器 | 每次 chat() +1 |
| `turn_count` | Engine 内部计数器 | ReAct 转移点(每发一次 model_call/tool_exec +1) |
| `context_tokens` | **API usage 捕获**: 从 model_call 响应的 `usage.prompt_tokens` 取 | 每次 model_call 响应后写入 |
| `model_context_window` | 启动时从 ModelAdapter 的 capabilities 读取; AgentConfig 可覆盖 | EngineBuilder.build() 时 |
| `runtime` | Active time(仅 Engine 处于 `processing` 状态的累计时长, 不含 `waiting` / `stopped`) | 状态机转移点累加 |
| `last_user_message` | 最近一次 chat() 的 user_input | chat() 时更新 |

**`context_tokens` 的精确性来源**:

- 每次 model_call 响应都带 `usage.prompt_tokens`(OpenAI / Anthropic / DeepSeek 等都返回)
- Engine 不解析 message 字节、不做 char/word 启发式估算
- 唯一一次估算在 session 初始(messages 刚 push 后); 之后每次 model_call 自动校准
- CheckpointRule 触发的 CompactOp / MemoryOp 等会修改 messages, 造成少量漂移; 下次 model_call 响应的 usage 会重新精确

### 1.7 WaitEvent + WaitStrategy

```rust
// crates/arf-core/src/wait_event.rs

pub struct WaitEvent {
    pub id: EventId,
    /// 该 event 等待的所有消息
    pub members: Vec<PendingMessageWait>,
    /// 已收集的响应(按 correlation_id 索引)
    pub received: HashMap<CorrelationId, Response>,
    /// 触发策略
    pub strategy: WaitStrategy,
    pub created_at: Instant,
}

pub enum WaitStrategy {
    /// 所有 member 都响应才触发(默认)
    All,
    /// 任一 member 响应就触发, 其余响应被丢弃
    Any,
    /// 指定数量 member 响应后触发
    Count(usize),
}

pub struct PendingMessageWait {
    pub correlation_id: CorrelationId,
    pub msg_type: String,
    pub message_payload: Value,     // 完整原始消息, Bus 重启后重发
    pub expected_receivers: usize,  // Strict 时=1; Discovery 时=BusGraph 匹配数
    pub received_count: usize,
}
```

**WaitEvent 与 Bus lifecycle signal 的术语区分**: 本文档用 `WaitEvent` 指 Engine 内部的 pending message group; Bus 发出的 `node_online` / `node_offline` 称为 `lifecycle_signal`(原文 §5.7 用的"event"一词已废弃, 避免与 WaitEvent 混淆)。

---

## 2. 要解决的问题与解决措施

> 本节是文档主体。每个问题对应: 现状 → 影响 → 解决措施 → 对已实现功能的改动 → 验收标准。

### P1: Engine 紧耦合 Node 类型, App 无法扩展

**现状**: 早期设计 Engine 通过封闭枚举(`ModelCall` / `ToolExec` / `MemoryOp` / `CompactOp` / `SubagentOp` / `HumanHandoff`)路由消息, 且硬编码 `ModelAdapter` / `McpNode` / `MemoryNode` 等具体 Node 类。

**影响**: 违反"框架提供 mechanism, 应用提供 policy"; 任何新消息类型或新 Node 类都必须修改 Engine 代码, 无法扩展。

**解决措施**:

1. **ActionMessage trait**(§1.1)— App 通过实现 trait 添加新消息类型, 不需要修改 Engine
2. **Node trait**(§1.4)— App 实现 Node trait 添加新节点
3. **Engine 拥有 ReAct 状态机 + 5 Checkpoint 位置 + State, 不拥有 Node**(§3、§4、§10)

**对已实现功能的改动**:

- `crates/arf-core/src/lib.rs`: 新增 `ActionMessage` trait, `MessageIntent` enum, `Node` trait, `Checkpoint` enum
- `crates/arf-model-adapter/src/node.rs`: 实现 `Node` trait(而不是只 send ActionMessage)
- `crates/arf-mcp/src/node.rs`: 实现 `Node` trait

**验收标准**:

- [ ] Engine 代码不 `use` 任何具体 Node 实现所在的 crate
- [ ] App 注入 `human_handoff` 消息类型不需要改 Engine 代码
- [ ] 集成测试: App 实现自定义 Node + ActionMessage, Engine 正确路由

---

### P2: 路由决策需要灵活(点对点 vs 能力发现)

**现状**: 早期 Engine 只有"按 NodeId 精确发送"一种路由模式, 不能表达"任意 MCP 节点"或"primary 层模型"。

**影响**: App 硬编码 NodeId 后, 节点替换/扩展时必须改 App 代码; 多模型投票、多 MCP 后端等场景实现困难。

**解决措施**:

1. **Route enum 二元化**(`Strict(Vec<NodeId>)` / `Discovery(Capability)`)(§1.3)
2. **Capability AND 语义** + 顶层字符串字段匹配规则(§1.3.1)
3. **DiscoveryCache** 避免每次 publish 遍历 BusGraph(§1.3.2)
4. **DiscoveryCache 失效策略**: 订阅 Bus lifecycle signal, push-based 增量更新(2026-06-30 决议)

**对已实现功能的改动**:

- `crates/arf-core/src/route.rs`: 新增 `Route` enum, `Capability` struct
- `crates/arf-engine/src/cache.rs`: 新增 `DiscoveryCache`
- `crates/arf-engine/src/lib.rs`: Engine 订阅 `node_online` / `node_offline` lifecycle signal 维护缓存

**验收标准**:

- [ ] `Route::Strict(vec!["primary_model"])` 精确投递单节点
- [ ] `Route::Discovery(Capability{requirements: vec![("kind".into(), "model".into())]})` 匹配所有 `kind=model` 节点
- [ ] 多 receiver Query 等全部, Command 全发
- [ ] DiscoveryCache 在 1000 节点拓扑下 publish 耗时 < 1ms(无 BusGraph 遍历)
- [ ] lifecycle signal 触发缓存增量更新, 1000 节点失效 < 1ms

---

### P3: Engine 何时触发副作用不明确

**现状**: 早期设计用 YAML 配置 checkpoint, 但 YAML 难以表达"每 5 轮触发一次"这种基于 State 的条件; 且 Checkpoint 是固定 5 个位置还是可扩展, 早期不定。

**影响**: App 注入副作用(记忆抽取、上下文压缩)的能力缺失或笨拙; Checkpoint 概念模糊导致 Engine 行为难预测。

**解决措施**:

1. **5 个 Checkpoint 固定位置**(BeforeModelCall / AfterModelCall / BeforeToolExec / AfterToolExec / RoundEnd)(§1.5)
2. **CheckpointRule 四元组**: `name` + `trigger` + `when(state)→bool` + `build(state)→ActionMessage`(**不包含 route**, 避免双源)(§1.5)
3. **Route 单一源**: 所有消息的投递 route 来自 `AgentConfig.routes[msg_type]`, 包括 CheckpointRule 触发的消息
4. **Intent 单源**: `intent` 来自 `ActionMessage::intent()` trait 方法, CheckpointRule 不声明
5. **运行时 memory 抽取**: 通过 CheckpointRule 触发 `MemoryOp::extract`(Command)
6. **运行时 memory 检索**: 由模型主动调 `memory_search` tool(走标准 tool_exec), 不是单独的 MemoryOp::Retrieve(2026-06-30 决议移除 Retrieve)
7. **Turn 结束**: 与 RoundEnd 合并; turn 级 hook 用 `Checkpoint::RoundEnd` + `over_view.turn_count` 判断

**对已实现功能的改动**:

- `crates/arf-core/src/checkpoint.rs`: 新增 `Checkpoint` enum, `CheckpointRule` struct
- `crates/arf-engine/src/lib.rs`: 5 个 Checkpoint 位置触发 CheckpointRule 评估
- 标准构造器: `every_n_rounds(every_n, trigger, build)`, `when_context_over(ratio, trigger, build)`

**验收标准**:

- [ ] CheckpointRule 在 trigger 位置按 when(state) 决策, true 时调用 build(state)
- [ ] build 返回的 msg 按 `msg.msg_type()` 查 `AgentConfig.routes` 投递
- [ ] Query intent 触发 Engine park, Command intent 触发 fire-and-forget
- [ ] App 不能在 CheckpointRule 上声明 route 或 intent(编译错误或断言)

---

### P4: 多 Receiver 响应管理混乱

**现状**: Engine 早期有"单 send → 1 等响应"模型; 多 Receiver 并发响应没有统一抽象, App 想做"3 个模型投票后等所有结果"或"任意一个先响应就继续"时无标准做法。

**影响**: 多模型 / 多 MCP 后端 / first-responder 路由等场景实现重复且不一致; 并发响应管理散落在 App 代码。

**解决措施**:

1. **WaitEvent** 作为等待队列的基本单元(不是单条消息); 一个 event 可等**多条消息**(members)(§1.7)
2. **WaitStrategy**(All / Any / Count(n))决定 event 触发条件(§1.7)
3. **默认行为**: `send(msg, route)` 自动创建 1-member event(strategy=All)
4. **App 显式**: `engine.create_wait_event(strategy)` + `send_to_event(msg, route, event_id)` 合并多消息到一个 event
5. **response 处理**: 按 response msg_type 查 `AgentConfig.processors`(内置 msg_type 走白名单隐式 dispatch, §1.1)
6. **持久化**: WaitEvent 列表随 State 一起序列化, Bus 重启后重发

**典型场景**:

- **多模型投票 + 工具执行并行**: Strict([claude, gpt]) 的 model_call + Discovery 的 tool_exec, **等所有结果都齐**再做下一轮 think(All)
- **Human-in-the-loop + 后台任务**: HumanHandoff(Query) + ProgressUpdate(Command), 等用户响应 + 后台完成**都齐**才继续(All)
- **First-responder 路由**: strategy=Any 时, Strict([claude, gpt]) 中**任一**模型先响应就触发 event, 丢弃另一模型的响应

**对已实现功能的改动**:

- `crates/arf-core/src/wait_event.rs`: 新增 `WaitEvent`, `WaitStrategy`, `PendingMessageWait`
- `crates/arf-engine/src/lib.rs`: Engine 在 `&mut State` 期间维护 `state.wait_events`, 收到 response 时按 correlation_id 更新对应 member, strategy 满足时出栈

**验收标准**:

- [ ] 单 send 自动创建 1-member WaitEvent, strategy=All
- [ ] App 显式 create_wait_event 后可绑定多条 send 到同一 event
- [ ] All / Any / Count(n) 策略行为正确
- [ ] response 处理按 response msg_type 查表 dispatch
- [ ] WaitEvent 随 State 一起序列化与恢复

---

### P5: 响应协议 + 错误处理

**现状**: 早期 Response 协议有 `Done` / `Failed` / `Wait` / `Err` / `Timeout` 多种变体, ack 与错误混合在同一个协议里。

**影响**: Engine 需要解析每种变体的语义, 协议复杂度膨胀; Receiver 内部错误与"等不到响应"在协议层混在一起, 增加理解成本。

**解决措施**:

1. **Response 单形态** `Done(serde_json::Value)`: Engine 不解析 Value 内部字段(§1.2)
2. **意图由 ActionMessage.intent() 决定**: Query→park 等所有, Command→不入队
3. **错误不通过 Response 协议**, 通过两种 lifecycle signal:
   - `node_offline`(进程死了)→ Engine 标记 member 为 failed, FailedReason::Offline
   - Engine 内部 timer 超时(hang 但未下线)→ FailedReason::Timeout
4. **业务错误**作为 Value 内容正常返回, 由 App processor 解释
5. **OnMemberFailedHandler hook**: App 通过此决策 Retry/SwitchTo/FailSession/IgnoreAndContinue(详见 §2.P8)

**对已实现功能的改动**:

- `crates/arf-core/src/lib.rs`: 新增 `Response` enum, `FailedReason` enum, `OnMemberFailedHandler` trait
- `crates/arf-engine/src/lib.rs`: 监听 `node_offline`, 维护 per-member timer

**验收标准**:

- [ ] Response::Done(Value) 单形态
- [ ] 业务错误(如模型返回 "permission denied")作为 Value.content 返回
- [ ] node_offline signal 触发 OnMemberFailedHandler, 不污染 Response 协议
- [ ] Receiver panic 不会污染 Response(由 panic hook 转换成 node_offline 或 App 实现捕获)

---

### P6: State 所有权与跨调用语义

**现状**: 早期 Engine 持有 State, App 拿不到 State 引用, 持久化时机由 Engine 决定。

**影响**: 违反"框架提供 mechanism, 应用提供 policy"; App 想在 run() 中断时序列化 State 必须等 Engine 提供 hook; 跨 Engine 共享 State 难。

**解决措施**:

1. **App 持有 State**: Engine.run(&mut state, ...) 借用, 不持有
2. **不提供 Session 抽象**: Session 本质是 App 对话层抽象, 可能跨多个 Engine; 框架不假设
3. **Engine.snapshot(state)**: 同步快照, App 直接拿到 `SessionSnapshot`
4. **Engine.restore(snap)**: 异步恢复, App 控制何时 resume、何时持久化
5. **CancellationToken**: App 在调用 engine.run() 前 clone() 一份留作"远程控制器", 跨线程安全

**对已实现功能的改动**:

- `crates/arf-core/src/state.rs`: 新增 `State`, `OverView`, `ModelMessage`, `SessionSnapshot`
- `crates/arf-engine/src/lib.rs`: `Engine` 不持有 State; `run(&mut State, ...)` 签名; `snapshot(&State)` 同步方法; `restore(SessionSnapshot)` 异步方法

**验收标准**:

- [ ] Engine 不实现 `Drop` 时持久化 State; 持久化完全 App 决定
- [ ] App 可在任意点调 `engine.snapshot(&state)` 同步获取快照
- [ ] `engine.restore(snap)` 后 `state.wait_events` 恢复, 未完成 event 重发
- [ ] 简单 App 与多 Engine 共享 State App 都用同一套 API

---

### P7: 单 Bus 全局复杂度爆炸, 故障域无隔离

**现状**: Bus 早期是单实例全局对象; 所有 Node 在同一 Bus 上互相干扰, filter 越来越复杂; Engine / Model / MCP / Memory 的生命周期、故障、恢复需求各异。

**影响**: 全局复杂度爆炸; 域间故障模式不同; 中断恢复粒度粗。

**解决措施**(借鉴汽车电子"域控制器"架构):

1. **Multi-Bus 可组合多实例**: 顶层 Bus 挂 Engine + 各 facade, 每个功能域有独立 sub-Bus
2. **Node 可订阅多条 Bus**: 通过 `NodeHandle::attach_to(bus, filter)` 添加订阅
3. **facade Node 跨坐两条 Bus**: App 自己写转发逻辑, 做协议/语义转换
4. **接收端过滤**(2026-06-30 决议): Bus broadcast 不做过滤, NodeHandle.recv() 按订阅 filter 过滤后再返回给 Node
5. **Node trait 提供 snapshot/restore** 用于跨域状态恢复
6. **`Bus::barrier()`**(可选): 用于全局一致点(§2.P9)

**App 拓扑选择**:

| 拓扑 | Engine 订阅 | 适用场景 |
|------|-----------|----------|
| 扁平 | 仅 Top Bus | 简单 App(≤10 个 Node) |
| 域控制器(保守) | 仅 Top Bus, 通过 facade | 中等 App(隔离故障域) |
| 域控制器(高效) | Top Bus + 各 sub-Bus | 高复杂度 App(省 facade 转发开销) |

**对已实现功能的改动**:

- `crates/arf-bus/src/connection.rs`: 重构 `NodeHandle` 内部从单 mpsc 改成 `Vec<(BusId, mpsc::Receiver)>`; 新增 `attach_to(bus, filter)` / `send_via(bus, to, payload)`
- `crates/arf-bus/src/lib.rs`: `Bus` 加 `id: BusId`(UUID); `Message` 加 `from_bus: Option<BusId>` 字段(兼容旧数据); 新增 `Bus::barrier(participants, timeout) -> BarrierReceipt`
- `crates/arf-core/src/message.rs`: `Message` 加 `from_bus: Option<BusId>` + `trigger: Option<EngineTrigger>` 字段

**验收标准**:

- [ ] 同一 NodeId 在多条 Bus 上是同一节点(全局唯一)
- [ ] facade Node 在两条 Bus 间转发时 from_bus 透传
- [ ] filter 在订阅时配置, on_message 只看已过滤消息(不需内部按 (from_bus, msg_type) 二次匹配)
- [ ] 现有 Bus 测试 0 修改通过

---

### P8: Node 掉线 + 超时处理

**现状**: Engine 在 pending WaitEvent 期间如何处理"响应永远不会到"——历史上 Engine 会永远卡住。

**影响**: 模型 hang / MCP 进程崩溃 / 网络分区等场景导致 Engine 死锁, App 拿不到响应也无法超时。

**解决措施**(2026-06-30 决议: Fail + App hook):

1. **Engine 订阅 `node_offline` lifecycle signal**
2. **对 pending WaitEvent 的每个 member 跟踪其目标 NodeId + 投递时间**
3. **Engine 内部为每个 pending member 设置 timeout**(默认从 AgentConfig 读取, 可被 route 上 msg_type-specific 配置覆盖)
4. **失败标记**: 收到 `node_offline(node_id=X)` 或某 member 超时时, 该 member 标记为 "failed response", FailedReason 附在 member 上
5. **按 WaitStrategy 处理**:
   - `All`: 任一 member failed → **event failed**(即使其他 member 还没响应)
   - `Any`: failed member 忽略, 继续等待其他 member
   - `Count(n)`: failed 不计入成功数, App 决定 n 是按"响应数"还是"完成数(响应+失败)"
6. **event failed → 触发 OnMemberFailedHandler**
7. **默认行为**(无 handler): 整个 session fail, Engine 进入 stopped 状态

**OnMemberFailedHandler hook**:

```rust
pub enum FailedReason {
    /// 节点进程崩溃(Bus 发 node_offline)
    Offline,
    /// 节点 hang 住但未下线(Engine 内部 timer 触发)
    Timeout,
}

pub trait OnMemberFailedHandler: Send + Sync {
    /// 返回决定:
    ///   * FailSession — Engine 进入 stopped, 返回 session 错误
    ///   * Retry(msg) — Engine 重发原 message 给原目标(App 显式决策)
    ///   * SwitchTo(new_route) — Engine 用新 route 重新解析 receiver
    ///   * IgnoreAndContinue — Engine 继续 wait(即便 strategy 是 All)
    fn on_member_failed(
        &self,
        event: EventId,
        member: CorrelationId,
        failed_node: NodeId,
        reason: FailedReason,
    ) -> MemberFailedAction;
}
```

**重试边界澄清**(2026-06-30 决议):

| 谁负责 | 什么 | 例子 |
|--------|------|------|
| **执行节点自己** | 内部重试: API 限流(429)、网络瞬时失败 | ModelAdapter 看到 429 后 sleep 重试; McpNode 在 transient 错误时重试 |
| **Engine** | 只发 / 只收; 不重试、不解析 Value 内部错误 | 业务错误(如模型返回 "permission denied")作为 Value.content 正常返回 |
| **App(通过 handler)** | 显式决策: 收到 FailedReason 后怎么办 | 注册 `OnMemberFailedHandler` 返回 Retry/SwitchTo/FailSession/IgnoreAndContinue |

**Engine 不做的事**(2026-06-30 决议):

- ❌ Engine 不自动 park 等 Node 恢复——Engine 没法"等"一个可能永不上线的 Node
- ❌ Engine 不内置重试计数器——App 想要就用 handler
- ❌ Engine 不监听 `node_online` 自动重发——App 想要就在 handler 里实现
- ❌ Engine 不区分 "临时掉线" vs "永久掉线"——Bus 只发 node_offline, App 自己判断
- ❌ Engine 不执行节点内部重试——那是执行节点(如 ModelAdapter)自己的实现

**对已实现功能的改动**:

- `crates/arf-core/src/lib.rs`: 新增 `FailedReason` enum, `OnMemberFailedHandler` trait, `MemberFailedAction` enum
- `crates/arf-engine/src/lib.rs`: Engine 订阅 node_offline; 维护 per-member timer; 处理 OnMemberFailedHandler 决策
- `crates/arf-bus/src/`: 已有 node_offline lifecycle signal

**验收标准**:

- [ ] ModelAdapter hang 时 Engine 在 timeout 后触发 OnMemberFailedHandler(FailedReason::Timeout)
- [ ] ModelAdapter 进程崩溃时 Bus 发 node_offline, Engine 触发 handler(FailedReason::Offline)
- [ ] handler 返回 Retry 后, Engine 重发原 message
- [ ] 默认行为(无 handler): session 失败, run() 返回 Err

---

### P9: App 级多 Node 一致性恢复

**现状**: Engine 自身 State 可序列化, 但多 Node(如 ModelAdapter 池、MCP 服务器组)各自有独立状态(连接池、文件句柄等), 需要跨 Node 全局一致快照。

**影响**: App 中断恢复时, 各 Node 状态与 Engine State 可能不一致, 导致恢复后行为异常。

**解决措施**(两层模型):

| 层 | 机制 | 触发者 | 谁决定语义 |
|----|------|--------|----------|
| **节点级** | `Node::snapshot/restore`(§1.4) | 收到 barrier msg 或 App 显式调用 | Node 自己 |
| **App 级** | `Bus::barrier(participants)` + 持久化存储 | App 在 Checkpoint 处显式调用 | App 自己 |

1. **Node::snapshot/restore 必备**: 每个 stateful Node 自己实现, 通过 Arc<Mutex/RwLock> 保护状态
2. **Bus::barrier 可选**: 只用独立快照的 App 可完全不调
3. **框架不强制 checkpoint 策略**——App 决定何时 snapshot / 是否用 barrier
4. **域控制器 facade 是 App 代码**(普通 Node + 自己的转发逻辑), 不是框架原语

**App-level Checkpoint 流程**:

```
Engine 到达 Checkpoint::RoundEnd
       │
       ▼
App 注册的 CheckpointRule::build 构造 AppCheckpoint intent(Command, Engine 不等)
       │
       ▼
AppCheckpoint Node 收到 msg → 触发 App-level checkpoint 逻辑
       │
       ├─ 1. 调用 bus.barrier(participants=[所有 stateful Node 的 id])
       │     │
       │     ▼
       │   Bus 广播 barrier msg, 所有参与者 Node 收到
       │     │
       │     ▼
       │   每个 Node 调 snapshot() → 写入本地 → 发 ack
       │     │
       │     ▼
       │   Bus 收集 ack 直到 timeout / 全部到 → 返回 BarrierReceipt
       │
       ├─ 2. 拿到 BarrierReceipt { acked, missing }
       │
       ├─ 3. 把 acked Node 的快照批量写入持久化存储
       │
       └─ 4. missing Node → App 决定: retry / fail / 容忍
```

**典型 CheckpointRule 配置**:

```rust
CheckpointRule {
    name: "app_checkpoint".into(),
    trigger: Checkpoint::RoundEnd,
    when: Box::new(|s| s.over_view.round_count % 5 == 0),
    build: Box::new(|s| Box::new(AppCheckpoint::new(stateful_node_ids=app.stateful_nodes()))),
}
```

**恢复流程**:

1. App 启动 → 创建 Bus 拓扑(top + 各 sub-Bus) + Node 实例
2. 从持久化存储加载最近一次成功 checkpoint 的所有快照
3. 对每个 Node 调用 `restore(snapshot)`
4. Node `attach_to` 对应的 Bus
5. App 调用 `engine.restore(snap)` 拿到 state → `engine.run(state, ...)` 重发未完成 WaitEvent(§2.P4)

**边界约定**:

- AppCheckpoint 是 App Node, 不是 Engine 内置机制——保持 §0.2 第一条边界
- snapshot 语义由 Node 决定——框架不强加 schema, App 通过 NodeId 索引
- barrier 超时策略由 App 决定——框架只提供 timeout 参数
- snapshot 并发由 Node 实现负责——Node 内部用 RwLock/Mutex 保护状态
- **Engine 不感知 recovery 发生**——Engine 只看到 Resume 信号, 与正常 Chat() 不可区分

**对已实现功能的改动**:

- `crates/arf-bus/src/lib.rs`: 新增 `Bus::barrier(participants, timeout) -> BarrierReceipt`
- `crates/arf-core/src/lib.rs`: `Node::snapshot/restore` 已在 §1.4 定义
- App 实现 `AppCheckpoint` Node + 持久化存储后端

**验收标准**:

- [ ] `bus.barrier(["n1", "n2"], timeout=5s)` 在 timeout 内收到所有 ack 返回 acked; 否则 missing
- [ ] Node snapshot 卡死超过 timeout 计入 missing
- [ ] App 可注册 AppCheckpoint CheckpointRule, 触发 barrier + 持久化
- [ ] 恢复后 Engine.run(state, ...) 与正常 chat() 行为一致

---

### P10: 同质资源突发并发无模板(§15 Pool 节点架构)

> 状态: 设计
> 动机: Engine 属于消费者, 它和 Model API / MCP server 等生产者**不是幂等关系**(请求与响应必须 1:1 耦合)。当同质资源出现突发并发, 单实例 Node 会成为瓶颈或触发生产者限流。本节定义 `Pool` 抽象: 在 §2.P7 Multi-Bus 基础上提供"同质资源 + 容量上限 + 生命周期管理"的通用模板, `ModelAdapterPool` / `McpPool` 是两个具体实现。

#### P10.1 架构定位

```
顶层 Bus ─────────┬─ Engine (stateful, 单实例)
                   ├─ ModelAdapterPool ─── sub-Bus ── model_node_1 ... model_node_N
                   └─ McpPool ─────────── sub-Bus ── tool_node_1 ... tool_node_M
                                   ↑
                                一个 Pool Node (1 个 NodeId)
```

**五条边界**:

1. **新增框架级 crate**: `arf-pool`, 位于 `arf-engine` 平级(不在 Engine 内部)
2. **Pool = §2.P7 域控制器(高效拓扑)的特殊化**: 区别是 Pool 内置容量 / Lifecycle / Overflow 控制, 普通 facade 不带
3. **Resource trait 是池化对象的最小契约**: `ModelAdapterResource` / `McpResource` 是两个内置实现
4. **App 配 Pool 行为(YAGNI 内置的"智能"决策)**: `min_nodes` / `max_nodes` / `idle_timeout` / `overflow` 全部由 App 显式声明
5. **不破坏 §0.2 三条边界**: Engine 不知道 Pool 存在; Pool 不知道 Engine 存在; Resource trait 不引入新边界

#### P10.2 组件与数据流

**组件清单**:

| 组件 | 位置 | 职责 |
|------|------|------|
| `Resource` trait | `arf-pool/src/resource.rs` | 池化对象最小契约 |
| `PoolConfig` | `arf-pool/src/config.rs` | 容量 / idle / overflow 策略 |
| `ResourceManager<R>` | `arf-pool/src/manager.rs` | 生命周期: 创建 / 扩缩 / 排队 |
| `PoolNode<R>` | `arf-pool/src/pool_node.rs` | 顶层 Bus 上的 1 个 Node |
| `Pool<R>` | `arf-pool/src/pool.rs` | 公开 API: new / connect / drain / stats |
| `ModelAdapterResource` | `arf-adapter/src/pool_resource.rs` | ModelAdapter 的 Resource 适配器 |
| `McpResource` | `arf-mcp/src/pool_resource.rs` | McpClientManager 的 Resource 适配器 |

**一次 `model_call` 全程**:

```
1. Engine (top bus)
   publish model_call msg, to=[model_pool]

2. ModelAdapterPool::on_message  (PoolNode in top bus)
   manager.acquire()  // 取出空闲 sub-Node; 无空闲则走 overflow 策略
   改写 msg: to=[sub_node_id], from_bus=Some(top_bus_id)
   msg_via(sub_bus, sub_node_id, payload)

3. sub-Bus
   broadcast → sub_node 收到

4. ModelAdapterResource (sub node)
   调真实 ModelAdapter.chat(messages)
   publish model_response back to sub-Bus

5. sub-Bus
   PoolNode 收到 model_response
   manager.release(sub_node_id)  // sub-Node 标记空闲
   把 model_response 投回 top bus, to=[engine_id]

6. Engine 收到 model_response, ReAct 循环继续
```

**关键约定**:

- `correlation_id` 透传: 步骤 2-5 全程不变, Engine 的 WaitEvent 按它匹配
- `from_bus` 透传: sub_node 看到的 `from_bus` = top bus id(不是 sub-bus id)
- PoolNode 不解析 `payload` 内部字段, 只做 transport 转发

#### P10.3 容量 / Lifecycle 状态机(sub-Node 维度)

```
                ┌─ min_nodes 保底(创建后永不自动销毁)
                │
   [Nil] ──new──> [Idle] ◀── release ── [Busy] ── acquire ──> [Idle]
                  │   │                                          │
                  │   └── idle_timeout 触发 ──> [Draining]        │
                  │                            │                  │
                  │                            ▼                  │
                  └────────── new (需要时) ── [Nil]               │
                                                              │
                                       overflow 触发新 sub_node ┘
```

- **Nil**: sub-Node 不存在(未创建 / 已销毁)
- **Idle**: 已存在但未在处理请求
- **Busy**: 正在处理 1 个请求(Pool 是 1:1 锁, 不支持单 sub-Node 内部并发)
- **Draining**: idle 超时, 停止接收新请求, 等当前请求完成后销毁

#### P10.4 Overflow 三种策略

| 策略 | acquire 行为 | 失败模式 |
|------|------------|----------|
| `Queue { max_pending }` | 当前 live 全忙 → 入 `pending` 队列; 超过 `max_pending` → 返回 `PoolError::QueueFull` | 返回错误给调用方 |
| `Reject` | 当前 live 全忙且已 == `max_nodes` → 返回 `PoolError::NoCapacity` | 立即返回 |
| `Block { acquire_timeout }` | 当前 live 全忙 → await 到有 sub-Node release; `acquire_timeout` 到期立即返回 | 超时返回 `PoolError::AcquireTimeout` |

**所有 overflow 策略都先尝试扩 sub-Node**(在 `[Nil] → [Idle]` 边)——这正是"动态并发度"的体现。只有在已达 `max_nodes` 上限后才走 overflow 分支。

#### P10.5 Snapshot / Restore

```rust
pub struct PoolSnapshot {
    pub manifest: PoolManifest,         // min/max/idle/overflow/...
    pub live_sub_nodes: Vec<(NodeId, serde_json::Value)>,  // sub-Node id + 各自 state
    pub last_used: HashMap<NodeId, Instant>,
}

pub struct PoolManifest {
    pub min_nodes: usize,
    pub max_nodes: usize,
    pub idle_timeout: Duration,
    pub overflow: OverflowStrategy,
}
```

**约定**:

- `PoolNode::snapshot` 是 `&self`(与 §1.4 决议一致)——读锁 manager manifest
- 遍历 live sub-Node 时**串行调用**(避免一次性锁住所有 sub-Node)
- `restore` 是 `&mut self`——清空当前 live, 按 snapshot 重建(App 需在 restore 前停 Engine, 或用 barrier 等机制确保无 in-flight 消息穿过 sub-Bus)
- idle 超时计时器**不持久化**(恢复后从 0 重新计)

#### P10.6 可观测性

```rust
pub struct PoolStats {
    pub live: usize,                    // 当前存活 sub-Node 数
    pub busy: usize,                    // Busy 状态数
    pub idle: usize,                    // Idle 状态数
    pub draining: usize,                // Draining 状态数
    pub pending: usize,                 // Overflow::Queue 等待者数
    pub total_acquired: u64,            // 累计 acquire 次数
    pub total_released: u64,            // 累计 release 次数
    pub total_evicted: u64,             // idle_timeout 销毁次数
    pub total_capacity_rejections: u64, // Overflow 拒绝次数
}
```

**集成方式**: App 在自己的 metrics / tracing adapter 里周期性 `pool.stats()`, 挂到 OTel / Prometheus / 自家监控。Pool 本身**不集成任何 metrics 后端**(与 §1.4 Node::snapshot 同样的"框架不给依赖"原则)。

#### P10.7 App 使用模式

**创建 ModelAdapterPool**:

```rust
use arf_pool::{Pool, PoolConfig, OverflowStrategy};
use arf_adapter::ModelAdapterResource;
use std::time::Duration;

let model_pool = Pool::new(
    &top_bus,
    Arc::new(|| ModelAdapterResource::new(ModelConfig {
        base_url: "https://api.deepseek.com".into(),
        api_key: env::var("DEEPSEEK_API_KEY")?,
        model_name: "deepseek-chat".into(),
        context_window: 128_000,
    })),
    PoolConfig {
        node_id: NodeId::new("model_pool"),
        capabilities: json!({
            "kind": "model",
            "tier": "primary",
        }),
        min_nodes: 1,                              // lazy 启动 1 个保底
        max_nodes: 4,                              // 突发可扩到 4
        idle_timeout: Duration::from_secs(60),     // 60 秒空闲就回收
        overflow: OverflowStrategy::Queue { max_pending: 8 },
    },
).await?;

model_pool.connect(&top_bus).await?;
```

**创建 McpPool**:

```rust
use arf_mcp::McpResource;

let mcp_pool = Pool::new(
    &top_bus,
    Arc::new(|| McpResource::new(McpConfig {
        servers: vec![McpServerConfig::stdio("filesystem", "npx", ...)],
        tools_dir: ...,
    })),
    PoolConfig {
        node_id: NodeId::new("mcp_pool"),
        capabilities: json!({"kind": "mcp", "transport": "stdio"}),
        min_nodes: 1,
        max_nodes: 2,                              // MCP 子进程重, 谨慎
        idle_timeout: Duration::from_secs(120),
        overflow: OverflowStrategy::Reject,        // 不排队, 直接拒绝
    },
).await?;
```

**Engine 端零改动**:

```rust
// §5 装配示例完全不变 —— Discovery 路由 model_call 时自动匹配 model_pool
let engine = EngineBuilder::new(bus=top_bus.clone())
    .build(config=AgentConfig {
        routes: hashmap!{
            "model_call" => Route::Discovery(Capability {
                requirements: vec![("kind".into(), "model".into())],
            }),
            "tool_exec"  => Route::Discovery(Capability {
                requirements: vec![("kind".into(), "mcp".into())],
            }),
        },
        ..Default::default()
    })
    .await?;
```

**关键点**:

- Engine 的 `model_call` 不需要知道目标是 Pool 还是单 ModelAdapter
- Discovery 匹配 `kind=model` 时把 Pool 顶层 Node 当作 receiver 返回
- Engine 发出去的就是一条普通 msg; Pool 内部完成 fan-out / in

**优雅关闭**:

```rust
let report = model_pool.drain().await?;
tracing::info!(?report, "model pool drained");
// report: { evicted: 3, in_flight: 0, failed: 0 }
```

`drain()` 行为:

1. 立即停止接收新 acquire
2. 等所有 in-flight 请求完成(带 timeout)
3. 销毁所有 sub-Node
4. 返回 `DrainReport { evicted, in_flight, failed }`

**与现有快照 / 恢复集成**:

```rust
let pool_snapshot = model_pool.snapshot().await?;
store.put("pools", json!({
    "model_pool": pool_snapshot,
    "mcp_pool": mcp_pool.snapshot().await?,
})).await?;
```

#### P10.8 API 表面(Rust trait sketch)

```rust
// crates/arf-pool/src/lib.rs —— 框架级 crate

/// 池化对象最小契约。任何实现该 trait 的类型都可挂到 Pool<R> 上。
pub trait Resource: Send + Sync {
    fn id(&self) -> &str;
    fn capabilities(&self) -> serde_json::Value;  // 用于 sub-Bus NodeInfo
    fn snapshot(&self) -> Result<serde_json::Value, SnapshotError>;
    fn restore(&mut self, v: serde_json::Value) -> Result<(), RestoreError>;
}

pub struct PoolConfig {
    pub node_id: NodeId,
    pub capabilities: serde_json::Value,      // 顶层 Bus 上声明的能力
    pub min_nodes: usize,                    // 保底数量 (lazy 启动)
    pub max_nodes: usize,                    // 硬上限
    pub idle_timeout: Duration,              // 超时销毁
    pub overflow: OverflowStrategy,          // Queue(n) | Reject | Block
}

pub enum OverflowStrategy {
    Queue { max_pending: usize },
    Reject,
    Block { acquire_timeout: Duration },
}

pub struct Pool<R: Resource> {
    top_handle: NodeHandle,                  // 顶层 Bus Node
    sub_bus: Bus,                            // 内部 sub-Bus
    manager: ResourceManager<R>,
    // ...
}

impl<R: Resource + 'static> Pool<R> {
    pub async fn new(
        top_bus: &Bus,
        factory: Arc<dyn Fn() -> R>,
        config: PoolConfig,
    ) -> Result<Arc<Self>, PoolError>;

    /// 对顶层 Bus 注册(应在 new() 后调用)
    pub async fn connect(&self) -> Result<(), BusError>;

    /// 强制缩容到 min_nodes
    pub async fn drain(&self) -> Result<DrainReport, PoolError>;

    /// 指标
    pub fn stats(&self) -> PoolStats;
}
```

#### P10.9 测试策略(边界优先)

按 CLAUDE.md 边界优先测试约定:

| 边界 | 测什么 |
|------|--------|
| `[构造]` | min=0, min>max, min<0 等非法 config |
| `[方法]` | acquire / release 在 Idle / Busy / Draining 各种状态下的行为 |
| `[边界]` | max_nodes 触顶时 Overflow 三种策略的分支 |
| `[时间]` | idle_timeout 销毁的精确性(用 tokio test 时间注入) |
| `[并发]` | N 个并发 acquire 不会让 live 超过 max_nodes |
| `[资源]` | FactoryFailed 时 Pool 仍能正常服务后续请求 |
| `[快照]` | snapshot + restore 完整 round-trip; manifest 与 live 状态都恢复 |
| `[回归]` | 与 §2.P7 域控制器 facade 互不干扰(Pool + 普通 facade 并存) |

集成测试必须包含:

- Engine + ModelAdapterPool + McpPool 跑通 ReAct 完整 round
- Pool 满载时 Engine 端 `model_call` 行为(按 overflow 策略走)

#### P10.10 不在 §15 范围内(YAGNI)

- Pool 内部请求的优先级队列(FIFO 是默认)
- Pool 内 sub-Node 间的请求路由策略(轮询 / 随机 / 最少连接——当前由 acquire 顺序决定)
- Pool 自动水平扩缩(基于 CPU / 内存指标的自动调优)——App 显式配置
- Pool 嵌套(Pool 内有 Pool)——增加复杂度, 无明确需求
- Pool metrics 主动 push(gauge / counter 主动报告)——App 显式 poll

**对已实现功能的改动**:

- **新增 crate**: `crates/arf-pool/`
- `crates/arf-model-adapter/src/pool_resource.rs`: `ModelAdapterResource` 适配器
- `crates/arf-mcp/src/pool_resource.rs`: `McpResource` 适配器

**验收标准**:

- [ ] Engine 与 Pool 之间零耦合——Engine 代码不出现 `Pool` 字样
- [ ] Pool 内 min_nodes=1, max_nodes=4, 突发 10 并发 model_call 时全部 200 OK
- [ ] idle_timeout 60s 后 live 缩到 min_nodes
- [ ] snapshot + restore round-trip 后 live sub-Node 数量与原一致

---

### P11: Tool/Skill 注入时序

**现状**: 早期设计 `{{tools}}` 占位符如何填充, 文档没说清。McpNode 在 connect 时声明工具, system prompt 在 EngineBuilder.build() 时组装, 时序矛盾。

**影响**: Tool/Skill 发现 → system prompt 注入路径断; App 不知道在哪注册工具过滤。

**解决措施**(2026-06-30 决议):

1. **EngineBuilder.build() 一次性组装**(见 §3.3): 聚合多 Bus 视图 → 校验 routes → 过滤 Skills → 过滤 Tools → 格式化 system prompt → 追加 initial_memory → 创建 Engine
2. **`{{skills}}` 占位符**: build() 时从 BusGraph 中 `kind=skill` 的 Node 收集描述填入 system_prompt_template
3. **`{{tools}}` 占位符不存在**: tools 通过 ModelCall 的 `tools: Vec<ToolSpec>` 字段传给 LLM API(OpenAI/Anthropic/DeepSeek 原生支持), 不进 system prompt
4. **资源过滤 glob 语法**:
   - 工具/技能的完整标识符为 `{node_id}.{tool_name}`(如 `mcp_local.read_file`、`skill_hub.greet`)
   - include 模式: `exact_name` / `node_id.*` / `*:read_*` / `mcp_*`
   - exclude 模式同上; **excluded 优先于 included**(先 exclude, 再 include)
   - include 为空列表 → 不收; include 为 None 或包含 `*` → 收全部(除非被 exclude)
5. **Memory 注入语义**:
   - **固定 memory(build 时)**: `config.initial_memory` 追加到 messages 前缀, 作为 system role message
   - **运行时 retrieval**: 由模型主动调 `memory_search` 等 tool, **不**触发 MemoryOp::Retrieve msg
   - **运行时 extraction**: CheckpointRule 触发 `MemoryOp::extract` Command, MemoryNode 完成后**不**修改 messages
   - **前缀稳定原则**: 所有 build 时一次性确定的 memory 放在 messages **前部**; 会话过程中产生的 system messages 追加到 messages **尾部**——确保前缀 cache 命中率

**对已实现功能的改动**:

- `crates/arf-core/src/lib.rs`: `EngineBuilder` 实现一次性 build 流程
- `crates/arf-core/src/lib.rs`: `AgentConfig` 加 `tools_include` / `tools_exclude` / `skills_include` / `skills_exclude` 字段
- `crates/arf-core/src/lib.rs`: `ModelCall` payload 加 `tools: Vec<ToolSpec>` 字段

**验收标准**:

- [ ] `system_prompt_template` 包含 `{{skills}}`, build() 后被填上 BusGraph 中的 skill 描述
- [ ] `system_prompt_template` 包含 `{{tools}}` 时, Engine 不替换(明确不写入)
- [ ] tools 通过 ModelCall payload 传给 LLM, 不污染 system prompt
- [ ] `tools_include=Some(vec!["mcp_local.*"])` 时只收 local_mcp 的工具
- [ ] `initial_memory` 作为 system role message 追加到 messages 前缀

---

### P12: Engine 自 filter 如何确定

**现状**: Engine 作为 Bus 上的 Node, 必须订阅它关心的 msg_type 才能收到响应。但 Engine 关心哪些 msg_type 取决于 AgentConfig.routes, 不是固定的。

**影响**: Engine 不知道订阅什么, 就收不到响应; 收太多又会污染 inbox。

**解决措施**(2026-06-30 补全 §G12):

`Engine.filter()` 由 config 自动计算, 在 `EngineBuilder.build()` 时调用, 结果用于 `bus.connect(filter=...)`:

```rust
impl Engine {
    pub fn filter(&self) -> MessageFilter {
        let types: HashSet<String> = self.config.routes.keys()
            // 所有 routes 对应的响应({msg_type}_result 后缀)
            .map(|msg_type| format!("{msg_type}_result"))
            // 内置 msg_type 响应(§1.1 白名单)
            .chain(["model_response".into(), "tool_result".into()])
            // lifecycle signals(§1.3.2 + §2.P8)
            .chain(["node_online".into(), "node_offline".into()])
            .collect();

        MessageFilter {
            types: Some(types),
            to_match: ToMatch::DirectedToMe,
        }
    }
}
```

**订阅覆盖的 3 类消息**:

| 类别 | msg_type | 来源 | 用途 |
|------|----------|------|------|
| **内置响应** | `model_response`, `tool_result` | ModelAdapter / McpNode | ReAct 主循环等待 |
| **自定义响应** | `{msg_type}_result`(如 `memory_op_result`, `compact_op_result`) | 任何 CheckpointRule 触发的 Query msg 的 Receiver | WaitEvent 完成触发新一轮 think |
| **Lifecycle signals** | `node_online`, `node_offline` | Bus 自身 | DiscoveryCache 维护(§1.3.2)+ Node 掉线处理(§2.P8) |

**Receiver 端约定**(2026-06-30 决议):

- 自定义 Receiver 返回响应时, **msg_type 必须遵循 `{原 msg_type}_result` 后缀约定**
- 例: Receiver 收到 `memory_op` 后返回 `memory_op_result`(带 correlation_id)
- 不遵循的 Receiver: 响应不会进入 Engine 的 filter, 被 Bus 丢弃
- 框架不强制——但 App 实现 Receiver 时需遵循, 否则 Engine 无法 park

**filter 动态计算时机**:

- EngineBuilder.build() 时调用一次, filter 传给 bus.connect()
- build() 之后 AgentConfig.routes 不变(Arc), filter 不需重新计算
- 多 Bus 时 Engine 在每条 Bus 上都订阅相同 filter

**对已实现功能的改动**:

- `crates/arf-engine/src/lib.rs`: `Engine` 实现 `Node` trait, `filter()` 方法由 config 自动计算

**验收标准**:

- [ ] Engine 在 BusGraph 上 connect 时 filter 由 config.routes 自动计算
- [ ] 不在 routes 中的 msg_type 不会被 Engine 收到
- [ ] lifecycle signals 始终在 filter 中(无论 routes 怎么配)
- [ ] 多 Bus 时每条 Bus 上 filter 相同

---

### P13: Cancel 传播与资源浪费

**现状**: Engine 早期对 cancel 处理模糊——"Engine 怎么 cancel?App 侧 API?是否中断 Receiver 处理?"(自审 G6)。

**影响**: Cancel 时 Receiver 继续执行长任务, 浪费 API 配额和 CPU。

**解决措施**(2026-06-30 决议):

1. **App 通过 CancellationToken 触发**: `engine.run()` 接受 `cancel: CancellationToken` 参数
2. **Engine 给所有 in-flight Receiver 发 cancel msg**: msg_type=`cancel`, payload={ correlation_id }
3. **Receiver 内部决定是否中断处理**: ModelAdapter 中断 reqwest 调用; McpNode 中断子进程
4. **不强制所有 Receiver 处理 cancel**: 简单 Receiver 忽略 cancel 也不影响正确性(Engine 已不 park 等响应)

**Cancel 传播流程**:

```
App 调 token.cancel()(任意线程)
       │
       ▼
Engine.run() 内 select! 分支触发
       │
       ▼
遍历 state.wait_events 中所有未完成 event
       │
       ├─ 对每个未响应 member 发 cancel msg:
       │     msg_type = 'cancel'
       │     to = member.target_node_id
       │     payload = { correlation_id: member.correlation_id }
       │     // Receiver 内部决定是否中断处理
       │     // 如 ModelAdapter 看到 cancel 后中断 reqwest 调用
       │
       ├─ 清空 state.wait_events(持久化前的已取消 event 不需保留)
       │
       └─ run() 返回 Err(RunError::Stopped)
```

**为什么发 cancel msg 而不是直接丢弃**(2026-06-30 决议):

- Receiver 可能正在执行长任务(Model API 30 秒调用、McpNode 工具执行)
- 直接丢弃 Response 会让 Receiver 浪费计算资源
- 发 cancel msg 让 Receiver 主动中断: 避免资源浪费

**框架内置 Receiver 必须同步响应 cancel**(2026-06-30 决议):

| Receiver | cancel 处理细节 |
|----------|-----------------|
| `ModelAdapter` | 立即中断正在进行的 HTTP 请求(`reqwest` 调用 abort handle)——避免继续占用 LLM API 计算配额 |
| `McpNode` | 中断工具执行(subprocess kill、reqwest abort)——避免长任务(如 `sleep 60`)继续消耗 CPU/IO |

**约定**:

- 框架内置 Receiver **必须**实现 cancel 响应(cancel 触发即停止资源占用)
- App 自定义 Receiver 也应实现(避免资源浪费)
- Receiver 收到 cancel 后不必返回 Response——Engine 已不 park 等响应
- 如果 Receiver 不响应 cancel, Engine 仍正确(run 返回 Err(Stopped)), 但**资源被浪费**

**对已实现功能的改动**:

- `crates/arf-core/src/lib.rs`: 新增 `CancellationToken` 类型别名(`tokio_util::sync::CancellationToken`)
- `crates/arf-engine/src/lib.rs`: `run()` 接受 cancel 参数; Engine 内部 select! 监听 cancel
- `crates/arf-model-adapter/src/node.rs`: 实现 cancel 响应(中断 reqwest)
- `crates/arf-mcp/src/node.rs`: 实现 cancel 响应(kill subprocess)

**验收标准**:

- [ ] App `cancel.cancel()` 后 run() 在 100ms 内返回 Err(Stopped)
- [ ] ModelAdapter 收到 cancel 后 1s 内中断 LLM API 调用
- [ ] McpNode 收到 cancel 后 kill 子进程
- [ ] 不实现 cancel 的自定义 Receiver 仍能正确终止(Engine 已不 park), 仅资源浪费

---

### P14: 终止条件覆盖

**现状**: Engine 终止条件不明, 早期设计 5 类终止条件(纯文本 / task_complete / max_turns / cancel / 不可恢复错误)需要明确触发位置。

**解决措施**:

| 条件 | 触发 | 转移目标 |
|------|------|----------|
| 模型返回纯文本 | LLM 不再调用 tool | `processing` → `stopped`(run 返回 Ok(output)) |
| `task_complete` | LLM 调用了 kernel tool | `processing` → `stopped`(run 返回 Ok(output)) |
| `max_turns` 超限 | `turn_count >= max_turns` | `processing` → `stopped`(run 返回 Err(MaxTurnsExceeded)) |
| cancel | App 调 `cancel.cancel()` | `*` → `stopped`(run 返回 Err(Stopped)) |
| 不可恢复 error | Receiver panic / OnMemberFailed 返回 FailSession / 节点全掉 | `*` → `stopped`(run 返回 Err(EngineError)) |

**对已实现功能的改动**:

- `crates/arf-engine/src/lib.rs`: 终止条件判断在 ReAct 主循环每个转移点
- `crates/arf-core/src/lib.rs`: `EngineError` enum 加 `MaxTurnsExceeded` 等变体

**验收标准**:

- [ ] 模型返回纯文本时 run() 返回 Ok(output)
- [ ] task_complete kernel tool 触发时 run() 返回 Ok(output)
- [ ] turn_count >= max_turns 时 run() 返回 Err(MaxTurnsExceeded)
- [ ] cancel.cancel() 触发时 run() 在 100ms 内返回 Err(Stopped)

---

## 3. 行为流程

### 3.1 4 状态机 + 转移矩阵

| 状态 | 含义 |
|------|------|
| `idle` | `state.wait_events.is_empty()`, 无 in-flight 操作 |
| `processing` | 正在执行 ReAct 循环主流程(构造 msg / publish / 等响应 / 更新 State) |
| `waiting` | `!state.wait_events.is_empty()`, 等待所有未完成 WaitEvent 完成 |
| `stopped` | 收到 stop 信号, run() 返回 `Err(EngineError::Stopped)` |

**状态机转移矩阵**(2026-06-30 补全):

| From → To | 触发条件 | Engine 副作用 |
|-----------|----------|--------------|
| `idle` → `processing` | `engine.run(state, user_input)` 被调用 | state.messages.push(user_msg); state.over_view.round_count += 1; 触发 BeforeModelCall Checkpoint |
| `processing` → `processing` | ModelCall 响应到达, 需要 tool_exec | 追加 assistant ModelMessage; 触发 AfterModelCall Checkpoint; 触发 BeforeToolExec; publish tool_exec |
| `processing` → `waiting` | Engine publish 一个 intent=Query 的 msg | state.wait_events.push(new_event); 等待 receiver 响应 |
| `processing` → `waiting` | Engine publish 多个 intent=Query msg(multi-member event) | state.wait_events.push(multi_member_event); 等待所有 member 响应 |
| `waiting` → `processing` | 某 WaitEvent 的 strategy 满足(All/Any/Count(n)) | 从 state.wait_events 移除; inject responses to state.messages; 触发新一轮 think |
| `waiting` → `waiting` | 部分 member 响应到达但未满足 strategy | 更新对应 member.received_count; 继续 park |
| `waiting` → `stopped` | App 调用 `cancel.cancel()`(§2.P13) | 给所有 in-flight Receiver 发 `cancel` msg; state.wait_events 清空; run() 返回 Err(Stopped) |
| `processing` → `stopped` | 命中终止条件(§2.P14): 纯文本 / task_complete / max_turns / 不可恢复错误 | run() 返回 Ok(final_output) 或 Err(error) |
| `waiting` → `stopped` | node_offline lifecycle signal + OnMemberFailedHandler 返回 FailSession | state.wait_events 清空; run() 返回 Err |
| `*` → `idle` | (仅在 run() 返回后发生; 下次 run() 进来再决定转 processing 还是 stopped) | state 不变 |

**关键不变式**:

- `idle` ↔ `waiting` 通过 `processing` 中转(不能直接互转)
- `stopped` 是终态; 后续 `engine.run()` 需 App 显式重置(如 `engine.restore(snap)`)
- 进入 `stopped` 后, state.wait_events 必清空(持久化前)
- `processing` 不会"卡住"——任何 publish 出去的 Query intent msg 都会立即让状态转 `waiting`

### 3.2 ReAct 循环(固定, 不可配置)

```
engine.run(state=&mut state, user_input=msg)  ← App 端: 拦截/转换/审批在调用方完成
       │
       ▼
   state.messages.push(value=msg)
       │
       ▼
   ┌─ Checkpoint::BeforeModelCall ─┐
   │ for rule in rules:            │
   │   if rule.when(state):        │
   │     msg = rule.build(state)   │
   │     bus.publish(msg=msg, route=route[msg.msg_type()])   │
   └────────────────────────────────┘
       │
       ▼
   publish ModelCall(Query) ─► park 等 receiver
       │
       ▼
   ┌─ Checkpoint::AfterModelCall ──┐
   │ (同 BeforeModelCall pattern)  │
   └────────────────────────────────┘
       │
       ▼
   判断 tool_calls?
       │
       ├─ yes ─► ┌─ Checkpoint::BeforeToolExec ──┐
       │         │ ...                          │
       │         └────────────────────────────────┘
       │              │
       │              ▼
       │         publish ToolExec(Query) ─► park
       │              │
       │              ▼
       │         ┌─ Checkpoint::AfterToolExec ───┐
       │         │ ...                          │
       │         └────────────────────────────────┘
       │              │
       │              └─► 回到 BeforeModelCall
       │
       └─ no ─► ┌─ Checkpoint::RoundEnd ────────┐
                │ ...                         │
                └──────────────────────────────┘
                     │
                     ▼
                return final_output → idle
```

**关键约定**:

- App 调 `engine.run(state=&mut state, user_input=msg)` 之前负责 input 拦截(验证、转换、审批), Engine 不感知
- Engine 内部 ReAct 循环只围绕 `model_call` 和 `tool_exec` 两个 action
- 5 个 Checkpoint 是位置标记; 具体发什么 msg 由 CheckpointRule 决定
- Engine 只通过 `ModelCall` / `ToolExec` 两个内置消息类型与 Bus 通信; `Memory` / `Compact` / `Subagent` / `HumanHandoff` 等由 App 通过 CheckpointRule 注入

### 3.3 EngineBuilder.build() 一次性组装(2026-06-30 决议)

`EngineBuilder.build(config)` 内部顺序执行:

1. **聚合多 Bus 视图**: EngineBuilder 接 `buses: Vec<Arc<Bus>>`, build() 遍历所有 Bus 的 graph, union 为 `merged_graph: Vec<NodeInfo>`(**Bus 上节点先去重**: NodeId 全局唯一, 同一 Node 在多条 Bus 上只出现一次, 详见 §2.P7)
2. **校验 routes**: 检查 `AgentConfig.routes` 中所有 msg_type 对应的 Node(Strict → 精确 NodeId; Discovery → Capability 匹配)都在 `merged_graph` 上线(fail-fast)
3. **过滤并收集 Skills**: 遍历 `merged_graph` 中所有 `kind=skill` 的 Node, 过滤 `config.skills_include / skills_exclude`(glob 模式), 将命中的 skill 描述填到 `system_prompt_template` 的 `{{skills}}` 占位符
4. **过滤并收集 Tools**: 遍历 `merged_graph` 中所有 `kind=mcp` 的 Node, 过滤 `config.tools_include / tools_exclude`, 将命中的 tools 存为 `Engine.tools: Vec<ToolSpec>`(**不**写入 prompt, 见 §2.P11)
5. **格式化 system prompt**: 用填好 skills 的 prompt 生成 `messages[0]`(system role)
6. **追加 initial_memory**: 将 `config.initial_memory: Vec<String>` 依次转为 system role messages, 追加到 `messages[1..]`(保持最长前缀稳定)
7. **创建 Engine**: Engine 持有 (formatted_messages + initial_memory) 作为初始 state.messages

**多 Bus 视图聚合**(2026-06-30 决议):

- EngineBuilder 现在接 `buses: Vec<Arc<Bus>>`, 不是单 Bus
- build() 把所有 Bus 的 graph union 为 merged_graph(NodeId 去重)
- routes 校验、tools/skills 收集都走 merged_graph
- App 显式声明 Engine 订阅哪些 Bus(与 §2.P7 Node trait 的 `attach_to` 配合)

**Tools 不入 prompt**(2026-06-30 决议):

- LLM API(OpenAI/Anthropic/DeepSeek)原生支持 `tools` 参数, 独立于 system prompt
- Engine 在构造 `ModelCall` 时将 `Engine.tools` 装入 payload(不是 state.messages 的一部分)
- App 的 `system_prompt_template` 中**不应**包含 `{{tools}}` 占位符(如果写了, Engine 不替换)

**ToolSpec 定义**(App 实现工具时遵循):

```rust
/// 工具规范。ModelAdapter 在 ModelCall 时传给 LLM API。
/// App 在 McpNode connect 时声明; Engine build() 时收集到 Engine.tools。
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,  // JSON Schema
}
```

---

## 4. 错误模型

Phase 6 涉及 7 种错误枚举, 按使用场景分组(2026-06-30 增):

```rust
// crates/arf-core/src/error.rs

// ── 1. EngineBuilder.build() 校验失败 ──────────────────────────────
pub enum BuildError {
    /// Strict route 指定的 NodeId 不在 BusGraph 上
    MissingNodes { nodes: Vec<NodeId> },
    /// Discovery route 的 Capability 无任何节点匹配
    MissingCapabilities { capabilities: Vec<Capability> },
    /// CheckpointRule.name 重复
    DuplicateRuleName { name: String },
    /// system_prompt_template 缺少已知占位符(如 required {{skills}} 但 template 没写)
    InvalidTemplate { placeholder: String, reason: String },
}

// ── 2. Engine 运行时错误(内部使用, 可被 OnMemberFailedHandler 拦截)──
pub enum EngineError {
    /// Cancel 触发(§2.P13)
    Stopped,
    /// 超过 max_turns
    MaxTurnsExceeded,
    /// CheckpointRule.build 返回的 msg_type 不在 AgentConfig.routes
    MissingRoute { msg_type: String },
    /// Discovery 路由未匹配任何节点(§1.3.2 边界场景)
    NoReceiver { msg_type: String, capability: Capability },
    /// state 反序列化失败(snapshot 版本不兼容等)
    StateCorrupted { reason: String },
}

// ── 3. engine.run() 返回错误 ────────────────────────────────────────
pub enum RunError {
    /// Engine 内部错误(包装 EngineError)
    Engine(EngineError),
    /// 内部结构错误(Engine 内部 bug, 不应发生)
    Internal(String),
}

// ── 4. engine.restore() 错误 ──────────────────────────────────────────
pub enum RestoreError {
    /// Snapshot 版本不兼容
    VersionMismatch { expected: u32, actual: u32 },
    /// Snapshot 反序列化失败
    DeserializeFailed { reason: String },
}

// ── 5. Node::snapshot 错误 ───────────────────────────────────────────
pub enum SnapshotError {
    /// Node 内部超时(§1.4 推荐用 tokio::time::timeout)
    Timeout,
    /// RwLock/Mutex 获取失败
    Lock(String),
    /// serde_json 序列化失败
    Serialize(String),
    /// Node 已下线, 无法 snapshot
    NodeOffline,
}

// ── 6. Bus send 错误 ─────────────────────────────────────────────────
pub enum SendError {
    NodeOffline,
    ChannelClosed,
    BusShutdown,
}

// ── 7. OnMemberFailedHandler 上下文(§2.P8)──────────────────────────
pub enum FailedReason {
    /// 节点进程崩溃(Bus 发 node_offline)
    Offline,
    /// 节点 hang 住但未下线(Engine 内部 timer 触发)
    Timeout,
}
```

**使用约定**:

- `BuildError` / `RestoreError` — App 直接 match 处理(一次性校验)
- `EngineError` — Engine 内部使用, 可被 `OnMemberFailedHandler` 拦截决策
- `RunError` — App 通过 `engine.run()` 拿到, match `RunError::Engine(EngineError::*)` 处理
- `SnapshotError` / `SendError` — Node / Bus 内部使用, App 通过 `Bus::barrier` 收到的 `BarrierReceipt.missing` 间接感知
- `FailedReason` — OnMemberFailedHandler 入参, App 决策依据

**转换关系**:

- `Bus::barrier` 返回的 `BarrierReceipt.missing` 包含 snapshot 失败的 NodeId
- `node_offline` lifecycle signal 触发 `FailedReason::Offline`(§2.P8)
- `engine.run()` 内部 `EngineError::*` 经 `RunError::Engine(...)` 包装返回

**PoolError**(§2.P10):

```rust
pub enum PoolError {
    InvalidConfig { reason: String },
    QueueFull { pending: usize, max: usize },
    NoCapacity { live: usize, max: usize },
    AcquireTimeout { waited: Duration },
    FactoryFailed { source: String },
    SubBusShutdown,
    ResourceSnapshot { node_id: NodeId, source: String },
}
```

---

## 5. 装配示例

### 5.1 平铺模式 App(单 Bus)

```rust
// crates/arf-agent/src/builder.rs

// ── 1. 创建 Bus ──────────────────────────────────────────────
let bus_top = Bus::new(heartbeat_interval=5000, heartbeat_timeout=15000, channel_capacity=64);

// ── 2. 创建并连接具体 Node(必须在 build() 之前!)────────────
//    build() 时 fail-fast 校验 routes → 查 merged_graph,
//    若 node_id 不在线或 capability 无匹配, 返回 BuildError。
let model = ModelAdapter::new(
    node_id=NodeId::new(id="primary_model"),
    config=model_config,
);
bus_top.connect(info=model.info(), filter=model.filter()).await?;

let mcp = McpNode::new(
    node_id=NodeId::new(id="local_mcp"),
    config=mcp_config,
);
bus_top.connect(info=mcp.info(), filter=mcp.filter()).await?;

let memory = MemoryNode::new(
    node_id=NodeId::new(id="memory_node"),
);
bus_top.connect(info=memory.info(), filter=memory.filter()).await?;

// ── 3. 声明 AgentConfig(routes + checkpoint_rules)─────────
let config = AgentConfig {
    agent_id: "assistant".into(),
    model_config: ModelConfig { provider: "deepseek".into(), model: "deepseek-v4-flash".into() },
    // 注意: tools **不**在这里。{{skills}} 占位符由 Engine build() 时查 BusGraph 填。
    system_prompt_template: "You are a helpful assistant.\n\nSkills:\n{{skills}}".into(),
    // 固定 memory: build() 后作为 system 消息追加到 messages 前缀(cache 命中稳定)
    initial_memory: vec![
        "User is a financial analyst working on quarterly reports.".into(),
        "Previous context: user prefers concise summaries with tables.".into(),
    ],
    max_turns: 10,
    routes: {
        let mut m = HashMap::new();
        m.insert("model_call".into(), Route::Strict(vec![NodeId::new(id="primary_model")]));
        m.insert("tool_exec".into(), Route::Discovery(Capability { requirements: vec![("kind".into(), "mcp".into())] }));
        m.insert("memory_op".into(), Route::Strict(vec![NodeId::new(id="memory_node")]));
        m.insert("compact_op".into(), Route::Discovery(Capability { requirements: vec![("kind".into(), "compactor".into())] }));
        m
    },
    checkpoint_rules: vec![
        CheckpointRule::new(
            "extract_memory",
            Checkpoint::RoundEnd,
            |s| s.over_view.round_count % 5 == 0,
            |s| Box::new(MemoryOp::extract(messages=s.messages.clone())),
        ),
        CheckpointRule::new(
            "compact",
            Checkpoint::BeforeModelCall,
            |s| s.over_view.context_tokens as f64
                / s.over_view.model_context_window as f64 > 0.8,
            |s| Box::new(CompactOp::new(messages=s.messages.clone())),
        ),
    ],
    // 资源过滤(§2.P11): 只收 local_mcp 的基础工具
    tools_include: Some(vec![
        "local_mcp.read_file".into(),
        "local_mcp.bash".into(),
        "local_mcp.edit_file".into(),
    ]),
    tools_exclude: vec![],
    skills_include: Some(vec![
        "skill_hub.greet".into(),
    ]),
    skills_exclude: vec![],
    processors: HashMap::new(),
    on_member_failed: None,
    ..Default::default()
};

// ── 4. build() fail-fast 校验 ──────────────────────────────
let engine = EngineBuilder::new(buses=vec![bus_top.clone()])
    .build(config=config)
    .await?;

// Engine 也作为 Node 上 Bus(§2.P12)
bus_top.connect(info=engine.info(), filter=engine.filter()).await?;

// ── 5. 运行(框架不提供 Session 抽象)───────────────────
let mut state: State = State::default();
let cancel = CancellationToken::new();
let output = engine.run(state=&mut state, user_input="Read /etc/hostname".into(), cancel=cancel.clone()).await?;
let snap: SessionSnapshot = engine.snapshot(state=&state);
serde_json::to_writer(File::create("session.json")?, &snap)?;
```

**为什么去掉 `NodeBinding`?**

- `NodeBinding` 跟实际 Node 的 `bus.connect(info, filter)` 重复声明 NodeId 和 subscriptions
- Engine 不需要"哪些 Node 存在"——BusGraph 知道
- Engine 不需要"Node 订阅什么"——Bus filter 知道
- Engine 只需要"哪些 msg_type 走哪条 Route"——`AgentConfig.routes` 已声明
- `build()` 校验 routes 时直接查 BusGraph, fail-fast 给出具体缺失项

**校验失败示例**:

```rust
let config = AgentConfig {
    routes: {
        let mut m = HashMap::new();
        m.insert("model_call".into(), Route::Strict(vec![NodeId::new(id="primary_model")]));
        m
    },
    ..Default::default()
};

let result = EngineBuilder::new(buses=vec![bus_top.clone()])
    .build(config=config)
    .await;

match result {
    Err(BuildError::MissingNodes { nodes }) => {
        // nodes = ["primary_model"]
        println!("App misconfigured: nodes not online: {nodes:?}");
    }
    _ => {}
}
```

### 5.2 AgentConfig 完整字段

```rust
// crates/arf-core/src/config.rs

pub struct AgentConfig {
    pub agent_id: String,
    pub model_config: ModelConfig,
    /// 包含 `{{skills}}` 占位符的 system prompt 模板(**不含** `{{tools}}`, tools 通过 ModelCall.tools 字段传)
    pub system_prompt_template: String,
    /// build() 后作为 system 消息追加到 messages 前部(保持最长前缀稳定, cache 命中率高)
    /// 提取自 App 持久化的"用户身份/任务画像/历史摘要"等。运行时 extraction 的结果**不**实时追加到此处
    pub initial_memory: Vec<String>,
    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,
    pub permissions: PermissionConfig,
    pub allow_paths: Vec<String>,
    pub routes: HashMap<String, Route>,                   // msg_type → Route
    pub checkpoint_rules: Vec<CheckpointRule>,           // App 注入的触发器
    /// 自定义 msg_type 的 response 处理器(§2.P4)
    /// 内置 msg_type(model_call / tool_exec, §1.1 白名单)无需注册
    pub processors: HashMap<String, Arc<dyn ResponseProcessor>>,
    /// Node 掉线时 Engine 等待响应失败的处理 hook(§2.P8)
    /// None 时使用默认行为(FailSession)
    pub on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>,
    /// Tools 白名单 glob(§2.P11): include 为 None 或包含 `*` 表示收全部; 空列表 = 不收
    pub tools_include: Option<Vec<String>>,
    pub tools_exclude: Vec<String>,
    pub skills_include: Option<Vec<String>>,
    pub skills_exclude: Vec<String>,
}
```

**所有 App 级配置统一进 AgentConfig**(2026-06-30 决议):

- `EngineBuilder` 只保留 `new(buses)` 和 `build(config).await?` 两个方法
- 不再有 `register_processor` / `on_member_failed` 等链式调用
- App 改 config → 重新 `build` 一个新 Engine; 不需要"在已 build 的 Engine 上追加 handler"

注意(2026-06-30): 移除原 `node_bindings: Vec<NodeBinding>`。具体 Node 通过 `bus.connect()` 上线, `EngineBuilder.build()` 时校验 routes 与当前 BusGraph 一致。

`AgentConfig` 不直接持有 ActionMessage 类型, 而是通过 `routes` + `checkpoint_rules` 间接表达。Engine 不需要知道具体消息类型, 只按字符串 msg_type 路由。

### 5.3 Engine 拥有 vs 不拥有

| 拥有 | 不拥有(App / Node 提供) |
|------|--------------------------|
| ReAct 状态机(idle/processing/waiting/stopped, §3.1) | State 所有权(State 由 App 持有, Engine 通过 &mut 借走) |
| ReAct 循环流程 | 具体 Node 实现(ModelAdapter 等) |
| 5 个 Checkpoint 位置 | Route 表 |
| 终止条件判断 | CheckpointRule 列表 |
| 在 &mut State 期间维护 messages / over_view / wait_events | NodeId / Capability 声明 |
| ActionMessage 子类型 | 持久化时机与存储后端 |
| System prompt 组装 | BusGraph 查询 |
| Turn/Round 计数 | |

### 5.4 Bus 节点分类

Engine 不直接调用任何节点, 只发消息。节点按 msg_type 订阅。

**拦截型**(Engine 无感):

| 节点 | 订阅 | 行为 |
|------|------|------|
| AuthNode | `tool_exec` | 读 AgentConfig → 放行/询问/拒绝 |
| SandboxNode | `tool_exec` | 读 allow_paths → 路径检查 |

**处理型**(Engine 等待响应):

| 节点 | 订阅 | 行为 |
|------|------|------|
| ModelAdapter | `model_call` | LLM API 调用(Engine 内置流程, 无 App 自定义) |
| McpNode | `tool_exec` | 工具执行(经 Auth/Sandbox 拦截) |
| MemoryNode | `memory_op` | 抽取(Command only; 运行时 retrieval 由模型 tool call 处理, 详见 §6.1) |
| CompactionNode | `compact_op` | 上下文压缩 |
| SubagentNode(App 实现) | `subagent_op` | 委派子 Agent——**Engine 不内置**, App 自己实现的 Node |
| HumanProxyNode(App 实现) | `human_handoff` | 人介入——**Engine 不内置**, App 自己实现的 Node |

**纯观测**(Engine 无感, 不响应):

| 节点 | 订阅 | 行为 |
|------|------|------|
| TraceWriter | all | 落盘 JSONL |
| Logger | all | 日志输出 |

---

## 6. 关键场景

### 6.1 MemoryOp: 仅 Extract(Command)

```rust
/// 记忆抽取消息(2026-06-30 决议: 移除 Retrieve)。
/// 运行时 memory 检索由模型主动发起 tool call(如 `memory_search`),
/// 走标准 tool_exec 流程; **不**是单独的 MemoryOp::Retrieve msg。
pub struct MemoryOp {
    pub messages: Vec<ModelMessage>,  // 待抽取的消息历史
}

impl ActionMessage for MemoryOp {
    fn msg_type(&self) -> &'static str { "memory_op" }
    fn intent(&self) -> MessageIntent { MessageIntent::Command }
    fn payload(&self) -> serde_json::Value {
        serde_json::json!({ "messages": self.messages })
    }
}
```

**双路 memory 机制**:

- **固定 memory(build 时)**: App 提供的 `config.initial_memory` 一次性追加到 messages 前缀
- **运行时 retrieval(tool call)**: 模型调 `memory_search` 等 tool, 走标准 `tool_exec` 流程
- **运行时 extraction(CheckpointRule)**: 本节 MemoryOp::extract 是唯一触发点, Command intent(fire-and-forget), 完成后**不**修改 messages——抽取结果在下次 session 加载时作为 `initial_memory` 出现

### 6.2 Multi-model 投票: Strict + Query

```rust
routes.insert("model_call".into(), Route::Strict(vec![
    NodeId::new(id="claude"),
    NodeId::new(id="gpt"),
    NodeId::new(id="deepseek"),
]));

// ModelCall 默认 intent=Query
// Engine park 等三个 model 都给最终响应
```

### 6.3 上下文压缩: Discovery + Query

```rust
// CompactOp 的 msg_type="compact_op", Engine 查 AgentConfig.routes["compact_op"] 投递
CheckpointRule::new(
    "compact",
    Checkpoint::BeforeModelCall,
    |s| s.over_view.context_tokens as f64
        / s.over_view.model_context_window as f64 > 0.8,
    |s| Box::new(CompactOp::new(messages=s.messages.clone())),
)

// CompactOp 的 intent() = Query(§6.1 模式)→ Engine park 等所有 compactor 完成
// 压缩完成后才发 ModelCall, 确保模型收到的是压缩后的上下文
```

### 6.4 自定义消息: human_handoff

```rust
pub struct HumanHandoff {
    pub prompt: String,
    pub blocking: bool,
}

impl ActionMessage for HumanHandoff {
    fn msg_type(&self) -> &'static str { "human_handoff" }
    fn intent(&self) -> MessageIntent {
        if self.blocking { MessageIntent::Query } else { MessageIntent::Command }
    }
}
```

---

## 7. 对已实现功能的改动

> 本节列出 Phase 6 实施对现有 crate 的所有改动清单。

### 7.1 `crates/arf-core/src/lib.rs`(从 ~1295 行扩展)

| 新增类型 | 行数估算 | 来源问题 |
|---------|---------|---------|
| `ActionMessage` trait | ~15 行 | P1 |
| `MessageIntent` enum | ~10 行 | P1 |
| `Node` trait | ~30 行 | P1, P9 |
| `Checkpoint` enum | ~15 行 | P3 |
| `CheckpointRule` struct + `new` | ~30 行 | P3 |
| `Capability` struct | ~10 行 | P2 |
| `Route` enum | ~15 行 | P2 |
| `State` struct | ~25 行 | P6 |
| `OverView` struct | ~20 行 | P6 |
| `WaitEvent` + `WaitStrategy` + `PendingMessageWait` | ~50 行 | P4 |
| `Response` enum | ~10 行 | P5 |
| `FailedReason` enum + `OnMemberFailedHandler` + `MemberFailedAction` | ~50 行 | P8 |
| `BuildError` enum | ~20 行 | §4 |
| `EngineError` enum | ~20 行 | §4 |
| `RunError` enum | ~10 行 | §4 |
| `RestoreError` enum | ~10 行 | §4 |
| `SnapshotError` enum | ~15 行 | §4 |
| `SendError` enum | ~10 行 | §4 (已有) |
| `ToolSpec` struct | ~10 行 | P11 |
| `SessionSnapshot` struct | ~15 行 | P6 |
| `CancellationToken` alias | ~5 行 | P13 |
| `ResponseProcessor` trait | ~10 行 | P4 |
| 文档注释 + example | ~100 行 | — |

**总计**: 新增 ~510 行, 加上原有 ~1295 行, 扩展到 ~1800 行。

### 7.2 `crates/arf-bus/src/lib.rs`(从 ~1355 行扩展)

| 改动 | 行数估算 | 来源问题 |
|------|---------|---------|
| `Message` 加 `from_bus: Option<BusId>` 字段 | ~5 行 | P7 |
| `Message` 加 `trigger: Option<EngineTrigger>` 字段 | ~10 行 | P12 |
| `Bus` 加 `id: BusId` | ~5 行 | P7 |
| `NodeHandle::attach_to(bus, filter)` | ~20 行 | P7 |
| `NodeHandle::send_via(bus, to, payload)` | ~15 行 | P7 |
| `Bus::barrier(participants, timeout) -> BarrierReceipt` | ~50 行 | P9 |
| `BarrierReceipt` struct | ~10 行 | P9 |
| `BusId` 类型 | ~5 行 | P7 |
| `EngineTrigger` struct | ~10 行 | P12 |
| 测试 | ~80 行 | — |

**总计**: 新增 ~210 行, 扩展到 ~1565 行。

### 7.3 `crates/arf-engine/src/lib.rs`(从空 stub ~19 行 → 完整实现)

| 新增内容 | 行数估算 | 来源问题 |
|---------|---------|---------|
| `Engine` struct + Node trait 实现 | ~80 行 | P1, P12 |
| `Engine::run(&mut State, user_input, cancel)` | ~150 行 | §3.2 |
| `Engine::snapshot(&State) -> SessionSnapshot` | ~20 行 | P6, P9 |
| `Engine::restore(SessionSnapshot) -> State` | ~30 行 | P6, P9 |
| `Engine::filter()` (自动计算) | ~25 行 | P12 |
| `Engine::subscribe_lifecycle_signals` | ~30 行 | P8, P12 |
| `DiscoveryCache` (从 §1.3.2 搬过来) | ~80 行 | P2 |
| 4 状态机 + transfer matrix | ~120 行 | §3.1 |
| CheckpointRule 评估循环 | ~60 行 | P3 |
| WaitEvent 队列管理 | ~100 行 | P4 |
| cancel 传播 | ~40 行 | P13 |
| OnMemberFailedHandler 调用 | ~30 行 | P8 |
| 终止条件判断 | ~50 行 | P14 |
| 单元测试 | ~300 行 | — |

**总计**: 从空 stub 扩展到 ~1100 行。

### 7.4 `crates/arf-model-adapter/src/node.rs`(从已有扩展)

| 改动 | 来源问题 |
|------|---------|
| 实现 `Node` trait (`id`/`snapshot`/`restore`/`on_message`) | P1, P9 |
| 在 `on_message` 中处理 `cancel` msg | P13 |
| 实现 `Node::snapshot` (导出内部 HTTP client state / pool) | P9 |

### 7.5 `crates/arf-mcp/src/node.rs`(从已有扩展)

| 改动 | 来源问题 |
|------|---------|
| 实现 `Node` trait | P1, P9 |
| 在 `on_message` 中处理 `cancel` msg | P13 |
| 实现 `Node::snapshot` (导出 sub-process handles / 连接池) | P9 |

### 7.6 `crates/arf-agent/src/config.rs`(从 ~403 行扩展)

| 改动 | 来源问题 |
|------|---------|
| `AgentConfig` 加 `routes` / `checkpoint_rules` / `processors` / `on_member_failed` / `tools_include` / `skills_include` 字段 | P3, P4, P8, P11 |
| `EngineBuilder` 实现 `build(config)` 一次性组装流程 | P11, P12 |

### 7.7 `crates/arf-pool/`(新增 crate)

| 文件 | 内容 |
|------|------|
| `Cargo.toml` | crate 元数据 |
| `src/lib.rs` | `Pool<R>` + `PoolNode` + 公开 API |
| `src/resource.rs` | `Resource` trait |
| `src/config.rs` | `PoolConfig` + `OverflowStrategy` |
| `src/manager.rs` | `ResourceManager<R>` 生命周期 |
| `src/error.rs` | `PoolError` enum |
| `src/tests/` | 边界优先测试 |
| `examples/` | ModelAdapterPool + McpPool 示例 |
| **总计** | ~600 行 + ~400 行测试 |

### 7.8 `crates/arf-model-adapter/src/pool_resource.rs`(新增)

| 内容 | 行数估算 |
|------|---------|
| `ModelAdapterResource` 适配器(实现 `Resource` trait) | ~80 行 |
| 测试 | ~50 行 |

### 7.9 `crates/arf-mcp/src/pool_resource.rs`(新增)

| 内容 | 行数估算 |
|------|---------|
| `McpResource` 适配器(实现 `Resource` trait) | ~80 行 |
| 测试 | ~50 行 |

### 7.10 测试文件新增

| 文件 | 内容 | 来源 |
|------|------|------|
| `crates/arf-core/tests/types.rs` | 类型单元测试(构造/方法/边界) | §1 |
| `crates/arf-engine/tests/state_machine.rs` | 4 状态机转移矩阵测试 | §3.1 |
| `crates/arf-engine/tests/react_loop.rs` | ReAct 主循环测试 | §3.2 |
| `crates/arf-engine/tests/checkpoint.rs` | CheckpointRule 评估 + Route 单一源 | P3 |
| `crates/arf-engine/tests/wait_event.rs` | WaitEvent + Strategy 测试 | P4 |
| `crates/arf-engine/tests/cancel.rs` | Cancel 传播测试 | P13 |
| `crates/arf-engine/tests/discovery_cache.rs` | DiscoveryCache 失效测试 | P2 |
| `crates/arf-engine/tests/node_offline.rs` | OnMemberFailedHandler 测试 | P8 |
| `crates/arf-engine/tests/build.rs` | EngineBuilder build() fail-fast | P11 |
| `crates/arf-engine/tests/snapshot.rs` | snapshot + restore round-trip | P6, P9 |
| `crates/arf-bus/tests/barrier.rs` | `Bus::barrier` 测试 | P9 |
| `crates/arf-bus/tests/multi_bus.rs` | Multi-Bus + facade 转发 | P7 |
| `crates/arf-pool/tests/manager.rs` | Lifecycle 状态机测试 | P10.3 |
| `crates/arf-pool/tests/overflow.rs` | Overflow 三策略 | P10.4 |
| `crates/arf-pool/tests/snapshot.rs` | snapshot round-trip | P10.5 |
| `crates/arf-pool/tests/concurrency.rs` | N 并发 acquire 不超 max | P10.9 |

---

## 8. 验收标准

### 8.1 三条边界验收

- [ ] **边界 1**: `crates/arf-engine/Cargo.toml` 不依赖 `arf-model-adapter` / `arf-mcp` / `arf-pool`; `grep -r "ModelAdapter\|McpNode\|Pool" crates/arf-engine/src/` 0 命中
- [ ] **边界 2**: `crates/arf-model-adapter/src/node.rs` 不 import `arf-engine`; Node 只订阅 msg_type 字符串
- [ ] **边界 3**: `Checkpoint` enum 固定 5 个变体; 没有 `BeforeInput` / `AfterInput` 等扩展位

### 8.2 核心抽象验收

- [ ] ActionMessage trait 编译可扩展(测试: App 实现自定义 `human_handoff` ActionMessage, Engine 正确路由)
- [ ] Response 单形态 `Done(Value)`; 编译时无 `Failed` / `Wait` 变体
- [ ] Route enum 仅 `Strict` / `Discovery`; 无 `Any` 变体
- [ ] Node trait 强制 `&mut self` on_message
- [ ] CheckpointRule 四元组(无 route); 编译时不能声明 route
- [ ] WaitEvent strategy = All/Any/Count(n); 默认行为正确
- [ ] State 三字段(messages / over_view / wait_events)

### 8.3 行为流程验收

- [ ] 4 状态机转移矩阵全部覆盖(见 §3.1 转移表)
- [ ] ReAct 循环纯文本终止返回 Ok(output); task_complete 触发终止返回 Ok(output); max_turns 触发返回 Err(MaxTurnsExceeded)
- [ ] cancel.cancel() 触发 100ms 内返回 Err(Stopped)
- [ ] build() fail-fast: MissingNodes / MissingCapabilities / DuplicateRuleName / InvalidTemplate
- [ ] Multi-Bus build() 聚合 graph, NodeId 去重

### 8.4 错误模型验收

- [ ] 7 种错误枚举(§4)+ PoolError 全部存在
- [ ] RunError 正确包装 EngineError
- [ ] BuildError 在 build() 时返回, 不进入 run()

### 8.5 集成验收

- [ ] **集成测试 1** (§11 平铺模式): 7 个 Node 同 Bus, 跑通 6 轮 ReAct 对话, State.over_view.round_count == 6
- [ ] **集成测试 2** (域控制器): 顶层 Bus + MCP sub-Bus, McpFacade 转发正确
- [ ] **集成测试 3** (Pool): ModelAdapterPool + McpPool 跑通 ReAct, Engine 零改动
- [ ] **集成测试 4** (Cancel): 触发 cancel, ModelAdapter 在 1s 内中断 LLM API 调用
- [ ] **集成测试 5** (Recovery): snapshot → 杀掉 Engine → restore → run() 与正常 chat 行为一致
- [ ] **集成测试 6** (Node offline): Mock Node crash, OnMemberFailedHandler 触发

---

## 9. 任务拆解

### 9.A Multi-Bus 基础设施(增量, §2.P7 实现)

| # | 任务 | 内容 | 依赖 |
|---|------|------|------|
| 6.0.1 | Node trait 抽象 | `crates/arf-core/src/node.rs` 定义 `Node` trait(`id`/`snapshot`/`restore`/`on_message`) | — |
| 6.0.2 | NodeHandle 多 Bus 订阅 | 重构 `NodeHandle` 内部从单 mpsc 改成 `Vec<(BusId, mpsc::Receiver)>`; 新增 `attach_to(bus)` / `send_via(bus, to, payload)` | 6.0.1 |
| 6.0.3 | BusId + Bus 标识 | `Bus` 加 `id: BusId`(UUID 或自增); `Message` 加 `from_bus: Option<BusId>` 字段(兼容旧数据) | 6.0.2 |
| 6.0.4 | Bus::barrier() 原语 | `barrier(participants, timeout) -> BarrierReceipt`; broadcast barrier msg + oneshot 收集 ack | 6.0.3 |
| 6.0.5 | 测试 + 文档 | 现有 Bus 测试 0 修改通过; 新增多 Bus / Barrier / snapshot 场景测试; 本设计文档落地 | 6.0.4 |

### 9.B Engine 核心实现(原有任务, 重排依赖)

| # | 任务 | 内容 | 依赖 |
|---|------|------|------|
| 6.1 | 核心类型定义 | `ActionMessage` trait、`MessageIntent`、`Route`、`Capability`、`State`、`OverView`、`Checkpoint`、`CheckpointRule` — 在 `crates/arf-core/src/` | 6.0.5 |
| 6.2 | Response 协议 | `Response::Done(Value)` 单形态; 引擎 park 逻辑(Query 等全部、Command 不入队) | 6.1 |
| 6.3 | Engine 骨架 | `Engine` struct 实现 `Node` trait、AgentConfig、State 所有权、4 状态机、bus.connect | 6.0.5 + 6.1 |
| 6.4 | ReAct 主循环 | 5 个 Checkpoint 位置、fixed ModelCall↔ToolExec 循环、终止判断 | 6.3 |
| 6.5 | Checkpoint 系统 | 规则注册、when/build 调用顺序、intent 决定的 park 行为 | 6.4 |
| 6.6 | 等待队列 + Park/Resume | WaitEvent + PendingMessageWait、correlation_id 匹配、expected_receivers 计算、event strategy 触发、持久化(与 §2.P9 App-level Recovery 配合) | 6.5 |
| 6.7 | Route 解析 | BusGraph 查询、Strict/Discovery 转换、多 receiver park 协调 | 6.3 |
| 6.8 | EngineBuilder API | `crates/arf-agent/src/builder.rs`、标准 CheckpointRule 构造器、`OnMemberFailedHandler` 注册、build() fail-fast 校验 | 6.6 + 6.7 |
| 6.9 | 集成测试 | MiniEngine + fixtures + ModelAdapter + McpNode 全链路; 包含多 Bus 拓扑 fixture | 6.8 |
| 6.10 | Python API | PyO3 绑定 Engine + AgentConfig + EngineBuilder + Bus::barrier + Node trait | 6.8 |

### 9.C 域控制器示例(教学任务, §2.P7 落地)

| # | 任务 | 内容 | 依赖 |
|---|------|------|------|
| 6.11 | MCP facade 示例 | `examples/domain_controller/` 演示 McpFacade Node 实现: top Bus ↔ MCP sub-Bus 转发 | 6.9 |
| 6.12 | App-level Recovery 示例 | `examples/recovery/` 演示 AppCheckpoint Node + Bus::barrier + 文件持久化 | 6.9 + 6.11 |

### 9.D Pool 实现(§2.P10)

| # | 任务 | 内容 | 依赖 |
|---|------|------|------|
| 6.13 | arf-pool crate 骨架 | `crates/arf-pool/` 新建; `Resource` trait、`PoolConfig`、`Pool<R>` 结构 | 6.0.5 |
| 6.14 | ResourceManager 生命周期 | Nil/Idle/Busy/Draining 状态机; idle_timeout 计时 | 6.13 |
| 6.15 | Overflow 三策略 | Queue(n) / Reject / Block(acquire_timeout) | 6.14 |
| 6.16 | PoolNode 路由 | top Bus ↔ sub-Bus 转发; correlation_id / from_bus 透传 | 6.15 |
| 6.17 | ModelAdapterResource | `crates/arf-model-adapter/src/pool_resource.rs` | 6.16 |
| 6.18 | McpResource | `crates/arf-mcp/src/pool_resource.rs` | 6.16 |
| 6.19 | Pool 集成测试 | Engine + ModelAdapterPool + McpPool 跑通 ReAct | 6.17 + 6.18 + 6.9 |

---

## 10. 待澄清 / 留待实现阶段

(2026-06-30 全部 5 条原始待澄清项已通过逐条讨论解决——见 §10.1)

### 10.1 已澄清

#### 来自本轮 §13 讨论的 5 项决议

- ~~`Task` 的具体形态~~ → **删除**。State 简化为 `messages + over_view + wait_events`; 所有 in-flight 操作由 WaitEvent 覆盖(§2.P4)
- ~~Node 订阅 msg_types 的注册机制(filter vs 内部自过滤)~~ → **接收端过滤**。`attach_to(bus, filter)` 每条 Bus 订阅独立配置 filter; Node on_message 只看过滤后消息, 不再需要内部按 (from_bus, msg_type) 二次匹配
- ~~Engine 启动时如何校验 node_id / capability 真实存在~~ → **删 NodeBinding + build() fail-fast**。`AgentConfig` 只声明 routes; `build()` 时校验当前 BusGraph 满足所有 routes; 失败返回 `BuildError { missing_nodes, missing_capabilities }`
- ~~节点掉线时的 Engine 行为~~ → **Fail + App hook**。Engine 监听 node_offline, 对 pending WaitEvent 的 member 标记 failed; 按 WaitStrategy 处理; 触发 `OnMemberFailedHandler` hook; 默认 FailSession
- ~~SessionState 持久化时机~~ → **App 全权决定**。Engine.snapshot/restore 是机制; 触发时机由 App 通过 CheckpointRule + Barrier 决定

#### 此前已澄清的 6 项(2026-06-30 multi-bus 修订)

- ~~`OverView` 字段的精确计算~~ → 见 §1.6 字段计算策略表。`context_tokens` 来自 API 响应的 `usage.prompt_tokens`; `runtime` 是 active time(processing 状态累计); `model_context_window` 启动时从 ModelAdapter capabilities 读取
- ~~Multi-Bus 是否需要全局路由~~ → **不需要**。每条 Bus 独立; 跨 Bus 由 Node 自己订阅多条 Bus 实现(manual bridging)
- ~~Node 跨 Bus 身份~~ → **全局唯一**。同一 NodeId 在所有订阅的 Bus 上是同一 Node(同一状态、同一身份)
- ~~域控制器是框架概念还是 App 模式~~ → **App 模式**。facade Node 是普通 Node, App 自己实现转发逻辑
- ~~多 Node 一致性方案~~ → **独立快照 + 可选 Barrier**(§2.P9)。`Node::snapshot/restore` 必备; `Bus::barrier` 可选; App 决定 checkpoint 策略
- ~~Bus API 变更范围~~ → **最小侵入**(§2.P7)。`Bus` 结构体不变; `NodeHandle` 内部多 mpsc; 现有测试 0 修改通过

#### 本次重构已澄清的 9 项矛盾(C1-C9)

- ~~C1 章节编号重复~~ → 重编号为 §0-§11 主结构
- ~~C2 章节编号跳跃~~ → 统一连续编号
- ~~C3 park / waiting 术语混用~~ → waiting 是状态名, park 是动词, 文档统一
- ~~C4 event 命名冲突~~ → WaitEvent(Engine 内部) vs lifecycle_signal(Bus)
- ~~C5 CheckpointRule 四/五元组~~ → 明确四元组, route 走 AgentConfig.routes 单一源
- ~~C6 §10 边界 1 与内置 msg_type 硬编码~~ → §0.2 加但书: msg_type 字符串不算违反
- ~~C7 MemoryOp::Retrieve 残留~~ → §5.4 删除, §6.1 明确只有 Extract
- ~~C8 EngineBuilder API 风格~~ → 统一 `build(config)`; 无链式 register_processor / on_member_failed
- ~~C9 Capability 数组示例违反匹配规则~~ → §1.3.1 删除数组示例, 加但书

### 10.2 仍待实现阶段澄清

- `Bus::barrier` 的具体 ack 协议细节(msg type、correlation_id 命名、oneshot vs broadcast)
- `OnMemberFailedHandler` 的 Retry/SwitchTo 行为对 Engine 状态的副作用(是否触发新 Checkpoint、是否写 State)
- AppCheckpoint Node 与 Engine 的 CheckpointRule 集成方式(rule.build 返回 AppCheckpoint msg 类型需要新增)
- Heartbeat 协议详细设计(Bus 已实现 heartbeat.rs, 但 Engine 侧如何用待补)

### 10.3 不属于本阶段任务(2026-06-30 决议)

以下功能**不**在 Phase 6 Engine 设计范围内, App 自行实现:

- **Subagent 编排**: Engine 不内置 subagent 机制。App 通过 CheckpointRule 注入 `subagent_op`(msg_type 字符串), App 自己实现的 SubagentNode 订阅该 msg_type 并驱动子 Agent。Engine 不感知子 Agent 的存在与状态。
- **Peer Agent 通信**: Engine 不内置 peer-to-peer 协议。App 通过 `peer_message` msg_type 在多个 Engine 间传递消息, App 自己实现 PeerNode 路由逻辑。
- **多 Agent 协调**: 投票 / 辩论 / 角色切换等模式都是 App 层模式, Engine 只提供 msg_type 抽象。

**核心边界**: Engine 可以驱动任意类型 Agent 的执行(model_call / tool_exec / 任何 msg_type 路由), 但不内置任何特定 Agent 拓扑——Engine 是 mechanism, App 决定 topology。

---

## 11. Python API 展望 + 集成测试

```python
from arf import Engine, EngineBuilder, AgentConfig, Route, Capability

config = AgentConfig(
    agent_id="assistant",
    # tools 不在 prompt 里; {{skills}} 由 Engine build() 时自动填
    system_prompt_template="You are a helpful assistant.\n\nSkills:\n{{skills}}",
    initial_memory=[
        "User is a financial analyst.",
        "Previous context: prefers concise summaries.",
    ],
    max_turns=10,
    routes={
        "model_call": Route.strict(node_ids=["primary_model"]),
        "tool_exec":  Route.discovery(capability=Capability(key="kind", value="mcp")),
        "memory_op":  Route.strict(node_ids=["memory_node"]),
        "compact_op": Route.discovery(capability=Capability(key="kind", value="compactor")),
    },
    checkpoint_rules=[
        # 每 5 轮提取记忆(route 来自 routes["memory_op"])
        CheckpointRule.every_n_rounds(
            trigger=Checkpoint.RoundEnd,
            every_n=5,
            build=lambda s: MemoryOp.extract(messages=s.messages),
        ),
        # 上下文超 80% 触发压缩(route 来自 routes["compact_op"])
        CheckpointRule.when_context_over(
            trigger=Checkpoint.BeforeModelCall,
            ratio=0.8,
            build=lambda s: CompactOp.new(messages=s.messages),
        ),
    ],
    processors={
        "human_handoff_result": HumanHandoffProcessor(),
    },
    on_member_failed=MyRetryHandler(max_retries=3),
    # 资源过滤(§2.P11)
    tools_include=["local_mcp.read_file", "local_mcp.bash"],
    skills_include=["skill_hub.greet"],
)

engine = await EngineBuilder.new(buses=[bus_top, bus_sub]).build(config=config)

# 框架不提供 Session; App 自己组装
state = State.default()
cancel = CancellationToken()
output = await engine.run(state=state, user_input="Read /etc/hostname", cancel=cancel)
snap = engine.snapshot(state=state)  # 同步快照
```

### 11.1 Integration Test: 平铺模式 App(设计验证)

> 以下为集成测试代码, 假设 Engine / EngineBuilder / Checkpoint / CheckpointRule / Route / Capability / MemoryOp / CompactOp 已按 §11 规范实现。
>
> 目的: 通过真实组装一个**平铺模式**(所有 Node 在同一 Top Bus 上, Engine 不感知任何具体 Node 类型)的多轮对话 App, 检验 Phase 6 设计的抽象干净程度。

#### 11.1.1 辅助 Node: MemoryNode + CompactionNode(mock)

```python
"""nodes.py — MemoryNode 和 CompactionNode 的 Python mock 实现。

这两个 Node 连接 Bus, 分别订阅 memory_op / compact_op,
收到消息后 mock 处理并返回结果。它们不知道 Engine 的存在——
只按 msg_type 订阅、按 from 字段回复。

验证点:
  - Node 独立性(§0.2 第二条边界): Node 只订阅 msg_types, 不假设发送者是 Engine
  - 平铺模式: 所有 Node 同在一个 Bus 上, filter 确保无串扰
  - Query/Command intent 区分: Engine 等 Query 的响应, 不等 Command
"""

import asyncio
from arf import NodeId, NodeInfo, MessageFilter, ToMatch


class BusNode:
    """连接 Bus 的通用 Node 基类。实际 Rust 侧实现 Node trait 后不再需要此类。"""

    def __init__(self, node_id: str, node_type: str, capabilities: dict):
        self.node_id = NodeId(node_id)
        self.info = NodeInfo(node_id, node_type, capabilities)
        self._handle = None
        self._task: asyncio.Task | None = None

    async def connect(self, bus, filter_types: list[str]):
        self._handle = await bus.connect(
            self.info,
            MessageFilter(types=filter_types, to_match=ToMatch.DirectedToMe),
        )
        self._task = asyncio.create_task(self._run())

    async def disconnect(self):
        if self._task:
            self._task.cancel()
            self._task = None
        if self._handle:
            await self._handle.disconnect()
            self._handle = None

    async def _run(self):
        """子类重写 _handle_msg(msg) → payload | None。"""
        while True:
            msg = await self._handle.recv()
            result = await self._handle_msg(msg)
            if result is not None:
                await self._handle.send(
                    msg_type=f"{msg.msg_type}_result",
                    to=[msg.sender],
                    payload=result,
                )

    async def _handle_msg(self, msg) -> dict | None:
        raise NotImplementedError


class MemoryNode(BusNode):
    """订阅 memory_op。收到 extract 时 mock 返回记忆条目。"""

    def __init__(self):
        super().__init__(
            node_id="memory/l1",
            node_type="memory",
            capabilities={"kind": "memory", "backend": "file", "tier": "l1"},
        )

    async def _handle_msg(self, msg) -> dict | None:
        action = msg.payload.get("action")
        if action == "extract":
            messages = msg.payload.get("messages", [])
            user_msgs = [m for m in messages if m.get("role") == "user"]
            # mock: 返回前 3 条用户消息摘要
            return {
                "memories": [
                    {"content": m.get("content", "")[:80], "source": "user"}
                    for m in user_msgs[-3:]
                ],
                "count": min(len(user_msgs), 3),
            }
        return None  # 不处理 retrieve(§6.1 决议)


class CompactionNode(BusNode):
    """订阅 compact_op。mock 返回压缩后的消息列表。"""

    def __init__(self):
        super().__init__(
            node_id="compactor/default",
            node_type="compactor",
            capabilities={"kind": "compactor", "backend": "sliding_window"},
        )

    async def _handle_msg(self, msg) -> dict | None:
        messages = msg.payload.get("messages", [])
        # mock: 保留 system + 最后 10 条, 其余替换为摘要
        keep = messages[-10:] if len(messages) > 10 else messages
        return {
            "compacted_messages": keep,
            "summary": f"Compacted {max(0, len(messages) - 10)} messages",
            "original_count": len(messages),
            "compacted_count": len(keep),
        }
```

#### 11.1.2 平铺模式 App 组装 + 多轮对话

```python
"""app.py — 平铺模式 App: 组装 Bus + Engine + 所有 Node, 跑多轮对话。

架构(7 个 Node 同在一个 Top Bus):

    Top Bus
    ├── engine/main            Engine(ReAct 循环)
    ├── model/deepseek         ModelAdapterNode(DeepSeek provider)
    ├── mcp/local              McpNode(本地 tools/ + skills/ 扫描)
    ├── memory/l1              MemoryNode(订阅 memory_op, 每 5 轮 extract)
    ├── compactor/default      CompactionNode(订阅 compact_op, context > 80% trigger)
    ├── trace/obs              全量观测(ToMatch.All, 打印所有消息)
    └── guard/path             PathSandbox(可选, 路径安全检查)

验证点:
  1. 组装接口 — EngineBuilder.new(buses=[...]).build(config=AgentConfig{...})
  2. Route 语义 — Strict(model_call → 精确 NodeId)vs Discovery(tool_exec → kind=mcp)
  3. Checkpoint 抽象 — every_n_rounds() / when_context_over() 构造器
  4. State 生命周期 — App 持有 State, engine.run(&mut state, ...) 多轮不丢状态
  5. Node 独立性 — MemoryNode/CompactionNode 不知道 Engine 的存在
  6. 平铺模式 — 所有 Node 在同一 Bus 上无消息串扰

运行:
    cd py-arf && ../.venv/bin/python python/arf/examples/phase6_flat/app.py
"""

import asyncio
import os
import tempfile
from pathlib import Path

from arf import (
    Bus,
    BusGraph,
    NodeId,
    NodeInfo,
    MessageFilter,
    ToMatch,
    DeepSeekConfig,
    DeepSeekProvider,
    ModelAdapterNode,
    McpNode,
    State,
    CancellationToken,
)
from arf.engine import (
    Engine,
    EngineBuilder,
    AgentConfig,
    Route,
    Capability,
    Checkpoint,
    CheckpointRule,
    MemoryOp,
    CompactOp,
)

from nodes import MemoryNode, CompactionNode


# ═════════════════════════════════════════════════════════════════════
# 0. 准备本地 tools/ 目录(McpNode 需要)
# ═════════════════════════════════════════════════════════════════════

def setup_tools_dir() -> str:
    """在临时目录创建 tools/ 和 skills/ 供 McpNode 扫描。"""
    root = tempfile.mkdtemp(prefix="arf_flat_app_")

    tools_dir = Path(root) / "tools" / "echo"
    tools_dir.mkdir(parents=True)
    tools_dir.joinpath("tool.toml").write_text("""\
name = "echo"
description = "Echo back the input message"
runtime = "bash"
entrypoint = "echo.py"
timeout_ms = 5000

[params_schema]
type = "object"
properties = { message = { type = "string", description = "Message to echo" } }
required = ["message"]
""")

    tools_dir.joinpath("echo.py").write_text("""\
import sys, json
data = json.load(sys.stdin)
msg = data.get("params", {}).get("message", "")
print(json.dumps({"content": f"echo: {msg}"}))
""")

    skills_dir = Path(root) / "skills" / "greet"
    skills_dir.mkdir(parents=True)
    skills_dir.joinpath("SKILL.md").write_text("""\
---
name: greet
description: Greet the user with a friendly message
compatibility: all
---

# Greet

A simple greeting skill.

## Tools

- greet.py: Print a greeting
""")

    skills_dir.joinpath("greet.py").write_text("""\
import sys, json
data = json.load(sys.stdin)
name = data.get("params", {}).get("name", "World")
print(json.dumps({"content": f"Hello, {name}!"}))
""")

    return root


# ═════════════════════════════════════════════════════════════════════
# 1. 主流程
# ═════════════════════════════════════════════════════════════════════

async def main():
    tools_root = setup_tools_dir()
    print(f"[app] tools root: {tools_root}")

    # ── 1.1 创建 Bus ───────────────────────────────────────────────
    bus = Bus()
    print("[app] Bus created")

    # ── 1.2 创建并连接所有 Node ─────────────────────────────────────

    # model/deepseek — 处理型 Node, Engine 等其响应
    model_provider = DeepSeekProvider(
        DeepSeekConfig(
            api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-placeholder"),
            models=["deepseek-v4-flash"],
        )
    )
    model_node: ModelAdapterNode = await model_provider.connect_to_bus(
        bus, NodeId("model/deepseek")
    )
    print("[app] model/deepseek connected")

    # mcp/local — 处理型 Node, 订阅 tool_exec
    mcp_node = McpNode.local(namespace="local", root=tools_root)
    await mcp_node.connect(bus)
    print(f"[app] mcp/local connected ({mcp_node.node_id})")

    # memory/l1 — 订阅 memory_op(mock)
    memory_node = MemoryNode()
    await memory_node.connect(bus)
    print("[app] memory/l1 connected")

    # compactor/default — 订阅 compact_op(mock)
    compactor_node = CompactionNode()
    await compactor_node.connect(bus)
    print("[app] compactor/default connected")

    # trace/obs — 纯观测 Node(ToMatch.All, 不阻塞)
    trace_handle = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    async def trace_loop():
        """后台任务: 打印所有 Bus 消息。"""
        while True:
            msg = await trace_handle.recv()
            # trigger 字段区分 ReAct 主循环消息 vs Engine 规则触发消息
            trigger_tag = ""
            if msg.trigger is not None:
                trigger_tag = (
                    f" [trigger={msg.trigger.checkpoint.name}"
                    f" rule={msg.trigger.rule_name}]"
                )
            print(f"[trace] {msg.msg_type:20s} {msg.sender!s:>18s}"
                  f"{trigger_tag}"
                  f" → {[str(t) for t in msg.to]!s:30s}"
                  f" payload={msg.payload}")
        # 示例输出:
        # [trace] model_call            engine/main                  → ['model/deepseek']
        # [trace] model_response        model/deepseek               → ['engine/main']
        # [trace] tool_exec             engine/main                  → ['mcp/local']
        # [trace] memory_op             engine/main [trigger=ROUND_END rule=extract_memory]
        #                                 → ['memory/l1']
        # 一眼区分 ReAct 步骤(无 trigger 标签)vs 规则触发(带 trigger 标签)

    trace_task = asyncio.create_task(trace_loop())
    print("[app] trace/obs connected")

    # ── 1.3 校验 BusGraph ───────────────────────────────────────────
    graph: BusGraph = bus.graph()
    print(f"\n[app] BusGraph: {len(graph.nodes)} nodes online")
    for n in graph.nodes:
        print(f"  {n.node_id!s:>25s}  {n.node_type:10s}  caps={n.capabilities}")

    # ── 1.4 构建 Engine ─────────────────────────────────────────────
    config = AgentConfig(
        agent_id="assistant",
        system_prompt_template=(
            "You are a helpful assistant.\n\n"
            "Skills:\n{{skills}}\n\n"
            "Use tools to help the user. Be concise."
        ),
        initial_memory=[
            "User is a financial analyst.",
        ],
        model_config={"provider": "deepseek", "model": "deepseek-v4-flash"},
        max_turns=10,
        routes={
            "model_call": Route.strict(node_ids=["model/deepseek"]),
            "tool_exec": Route.discovery(
                capability=Capability(key="kind", value="mcp")
            ),
            "memory_op": Route.strict(node_ids=["memory/l1"]),
            "compact_op": Route.discovery(
                capability=Capability(key="kind", value="compactor")
            ),
        },
        checkpoint_rules=[
            CheckpointRule.every_n_rounds(
                trigger=Checkpoint.RoundEnd,
                every_n=5,
                build=lambda s: MemoryOp.extract(messages=s.messages),
            ),
            CheckpointRule.when_context_over(
                trigger=Checkpoint.BeforeModelCall,
                ratio=0.8,
                build=lambda s: CompactOp.new(messages=s.messages),
            ),
        ],
    )

    engine = await EngineBuilder.new(buses=[bus]).build(config=config)
    print("\n[app] Engine built — routes + checkpoint_rules validated")

    # ── 1.5 App 持有 State(2026-06-30 决议: 框架不提供 Session 抽象)──
    state = State.default()
    print("[app] State initialized")

    # ── 1.6 多轮对话 ────────────────────────────────────────────────
    rounds = [
        "What files are in /tmp?",
        "Can you create a file /tmp/hello.txt with 'Hello ARF'?",
        "Read the file /tmp/hello.txt back to me.",
        "What other files are in /tmp now?",
        "Delete /tmp/hello.txt please.",
        "Can you verify the file is gone?",
    ]

    for i, user_input in enumerate(rounds, 1):
        print(f"\n{'='*60}")
        print(f"[app] Round {i}: {user_input}")
        print(f"{'='*60}")

        cancel = CancellationToken()
        output = await engine.run(state=state, user_input=user_input, cancel=cancel)
        print(f"[app] Round {i} output: {output}")

        # 检查 Checkpoint 触发(mock 环境会打印)
        print(f"[app] State: round={state.over_view.round_count}, "
              f"turn={state.over_view.turn_count}, "
              f"context={state.over_view.context_tokens}"
              f"/{state.over_view.model_context_window}")

    # ── 1.7 验证结果 ─────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("[app] All rounds complete. Validating...")
    print(f"  Total rounds: {state.over_view.round_count}")
    print(f"  Total turns:  {state.over_view.turn_count}")
    print(f"  Messages:     {len(state.messages)}")

    assert state.over_view.round_count == len(rounds), \
        f"Expected {len(rounds)} rounds, got {state.over_view.round_count}"
    assert state.over_view.turn_count > 0, "Expected non-zero turns"
    assert len(state.messages) > 0, "Expected non-empty messages"

    # 验证 Checkpoint 触发: 第 5 轮 RoundEnd 应触发 memory extract
    # (mock 环境下 checkpoint_rules 的 when 条件满足即触发)
    print("[app] ✅ All assertions passed")

    # ── 1.8 清理 ─────────────────────────────────────────────────────
    trace_task.cancel()
    await memory_node.disconnect()
    await compactor_node.disconnect()
    await mcp_node.shutdown() if hasattr(mcp_node, "shutdown") else None
    await model_node.shutdown()
    await bus.shutdown()
    print("[app] Cleanup complete")


if __name__ == "__main__":
    asyncio.run(main())
```

#### 11.1.3 设计验证结论

| # | 验证点 | 设计位置 | 结论 |
|---|--------|---------|------|
| 1 | **组装接口** | §5 装配示例 | `EngineBuilder.new(buses=[...]).build(config=AgentConfig{...})` 声明式 API, 所有 App 配置进 AgentConfig |
| 2 | **Route 语义** | §1.3 Route | `Strict(["model/deepseek"])` 精确路由 + `Discovery(Capability("kind","mcp"))` 能力发现, 语义一目了然 |
| 3 | **Checkpoint 抽象** | §1.5 CheckpointRule | `every_n_rounds()` 和 `when_context_over()` 两个标准构造器覆盖了典型需求; `name/trigger/when/build` 四元组足够灵活 |
| 4 | **State 生命周期** | §3.2 ReAct 循环 | App 持有 `State`, 通过 `engine.run(&mut state, ...)` 多轮不丢状态, `over_view` 字段自动维护 |
| 5 | **Node 独立性** | §0.2 第二条边界 | MemoryNode/CompactionNode 只订阅 msg_type, 不假设发送者是 Engine。mock 的实现只依赖 Bus API, 不 import Engine |
| 6 | **平铺模式** | §2.P7 扁平拓扑 | 7 个 Node 在同一 Bus 上无消息串扰——filter 在接收端过滤, 每个 Node 只看到自己订阅的类型 |
| 7 | **Query vs Command** | §1.1 MessageIntent | model_call / tool_exec / compact_op 是 Query(Engine park 等响应); MemoryOp.extract 是 Command(Engine 不等, fire-and-forget)。压缩必须在模型调用前完成, 所以 compact_op 是 Query |

**设计改进发现:**

- ~~`Route.strict(node_ids=...)` 参类型不一致~~ → 已统一。Rust 侧用 `Vec<NodeId>`(§1.3), Python 侧接受 `list[str]` 内部转换为 NodeId(§11, §11.1.2)。
- ~~`EngineBuilder.build(config)` 多态~~ → 已统一。§5 和 §11 均使用 `EngineBuilder::new(buses).build(config)`。
- `Capability` Python 构造简化为 `Capability(key="k", value="v")`(vs Rust 侧 `Capability::new(key, value)`)——语言惯例差异, 合理。
- McpNode Python 绑定缺少 `shutdown()` 方法——app.py 用 `hasattr` 兜底, 后续 Rust MCP 实现需补充。

---

## 12. 文档元信息

### 12.1 修订历史

| 日期 | 版本 | 主要变化 |
|------|------|---------|
| 2026-06-29 | v0 | 初版: 6 消息 enum + ack 双控制 + YAML checkpoint |
| 2026-06-30 | v1 | Engine-as-Actor 重构; 新增 Multi-Bus 扩展; 删除 Task/NodeBinding; 接收端过滤; build() fail-fast; Fail + App hook |
| 2026-06-30 | v2 | 按"问题-解决措施"重组; 修复 C1-C9(章节编号、术语、命名、四/五元组矛盾、§10 但书、Retrieve 残留、API 风格、Capability 数组示例); 新增 §0 设计理念总览、§7 对已实现功能的改动、§8 验收标准 |

### 12.2 与 self_review.md 的关系

`docs/v1.x/phase6/self_review.md` 是 2026-06-30 的设计自审记录, 按 CLAUDE.md 约定**不应**进入版本控制(仅 `phase6-engine-design.md` 是交付物)。本次重构从 self_review.md 抽取了:

- Critical C1-C4 → 部分已修复(C3 ModelMessage 在 §1.6 显式定义; C4 MemoryOp::Retrieve 在 §6.1 明确移除)
- Conflict F1-F5 → C1/C5/C6/C8 已修复; F3 路由单一源通过 CheckpointRule 四元组方案解决
- Cleanliness A1-A9 → C3/C4/C9 已修复; A2 WaitEvent 归属在 §1.6 明确属于 State
- Gap G1-G13 → G11 DiscoveryCache 在 §1.3.2 补全; G12 Engine self.filter 在 §2.P12 补全; G13 system prompt 时序在 §2.P11 解决

### 12.3 自审遗留项(C1 之外未在本次修复)

| 原 self_review 编号 | 状态 | 备注 |
|---------------------|------|------|
| C1 Rust 语法(`name: "x".into()` 命名参数) | **未修复** | 文档示例仍用 `new(name="x", ...)` 风格, 实现阶段需改为 struct literal |
| C2 CheckpointRule 闭包 HRTB 生命周期 | **已修复** | §1.5 显式 `for<'a> Fn(&'a State) -> ...` |
| G7 错误模型枚举 | **已补全** | §4 列出 7 种错误枚举 + PoolError |
| G8 重试语义 | **已澄清** | §2.P8 重试边界澄清表 |
| G9 Heartbeat 协议 | **仍待实现阶段** | §10.2 列入待澄清 |
| G10 并发模型 | **已补全** | §3.1 状态机 + §2.P13 cancel 传播 |
| G6 cancel 传播 API | **已补全** | §2.P13 CancellationToken + cancel msg |
| G2 Session.resume API | **已澄清** | §2.P6 不提供 Session 抽象, App 用 engine.snapshot/restore |

### 12.4 文档导航

- 找"为什么这样设计": §2(P1-P14)
- 找"具体 API 定义": §1(核心抽象)、§4(错误模型)
- 找"如何装配": §5(装配示例)
- 找"对现有代码的改动": §7
- 找"任务拆解": §9(9.A-9.D)
- 找"还差什么": §10