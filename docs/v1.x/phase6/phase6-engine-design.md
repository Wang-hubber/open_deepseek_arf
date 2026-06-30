# Phase 6 — Engine 设计

> 依赖：Phase 1 (Bus), Phase 4 (ModelAdapter), Phase 5 (MCP), arf-state
> 状态：设计（重构中）
> 取代：原 6 消息 enum / ack 双控制 / YAML 检查点方案
>
> **2026-06-30 修订（v2）**：基于 §13 逐条讨论的 5 项决议——删除 Task 抽象、删除 NodeBinding（build() fail-fast 校验）、filter 改接收端过滤、新增 §5.7 Node 掉线处理（Fail + App hook）、持久化时机 App 全权。
>
> **2026-06-30 修订（v1）**：新增 §1.5 Multi-Bus 架构扩展、§5.6 App-level Recovery 模型。Bus 从"单实例"扩展为"可组合多实例"，Engine-as-Actor 核心设计不变，仅 Bus 层增加最小原语（Node trait + 多 Bus 订阅 + Barrier）。

## 1. 核心思想

Engine 做减法。它是 Bus 上的一个 Actor：维护 AgentConfig + State，在 ReAct 循环中按订阅式触发器收发消息。

**所有外部交互走 Bus，没有例外。** Engine 不直接调 Provider、不直接调 HookRunner、不直接调 MCP。它甚至**不知道任何具体节点类型的存在**。

```
┌──────────────────────────────────────┐
│  Engine (Actor)                      │
│                                      │
│  AgentConfig  +  State               │
│    ├── messages  (Vec<ModelMessage>) │
│    └── over_view (OverView)          │
│                                      │
│  5 个 Checkpoint 位置（固定）        │
│      ↓                               │
│  CheckpointRule 列表（App 注册）     │
│      when(state) → bool              │
│      build(state) → ActionMessage    │
│      route  → Strict | Discovery     │
│                                      │
│  inbox: PriorityQueue                │
│         │                            │
│         ▼                            │
│  ┌─────────────┐  4 状态:            │
│  │   state     │  idle/processing/   │
│  │   machine   │  waiting/stopped    │
│  └──────┬──────┘                     │
│         │                            │
│  ┌──────┴──────┐  waiting queue     │
│  │  WaitEvent  │  持久化 + 重发     │
│  └──────┬──────┘                     │
│         │                            │
│         ▼                            │
│       Bus                             │
└──────────────────────────────────────┘
```

**依赖方向（单向，无循环）：**

```
                  arf-core (纯数据：ActionMessage trait, Route, State)
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
```

## 1.5 Multi-Bus 架构扩展（增量）

> 2026-06-30 修订。基于汽车电子"域控制器"架构的启示，把 Bus 从"单实例全局对象"扩展为"可组合多实例"。Engine-as-Actor 核心（§2-§11）保持不变；本节定义 Bus 层最小新增原语与 App 组合模式。

### 1.5.1 动机

**Agentic App = Bus(es) + Nodes**

App 级别中断恢复的关键是：**每条 Bus 上有状态 Node 的状态同步与持久化**（当前仅 Engine 有状态）。随着 App 复杂度增长，单 Bus 拓扑暴露出三个问题：

| 问题 | 表现 |
|------|------|
| 全局复杂度爆炸 | 所有 Node 在同一 Bus 上互相干扰，filter 越来越复杂 |
| 域间故障模式不同 | Engine / Model / MCP / Memory 的生命周期、故障、恢复需求各异 |
| 中断恢复粒度粗 | 单 Bus 状态即 App 状态，无法按域隔离恢复 |

**汽车电子域控制器架构的启示**：现代汽车电子从单一 CAN 总线演进到分层多总线：

```
顶层 Bus ──┬── 动力域 facade ──┬── sub-Bus ── ECU-A, ECU-B, ECU-C
           ├── 底盘域 facade ──┼── sub-Bus ── ECU-D, ECU-E
           └── 娱乐域 facade ──┴── sub-Bus ── ECU-F, ECU-G, ECU-H
```

- 顶层 Bus 只接各功能域的 **facade Node**（抽象）
- 每个功能域有独立 sub-Bus，承载该域的具体 **ECU Node**（具体）
- facade Node 跨坐两条总线，做协议/语义转换

**类比到 ARF**：

```
顶层 Bus ──┬── Engine（stateful，单独存在）
           ├── MCP-facade ──────── sub-Bus ── read_file, bash, edit_file, ...
           ├── ModelAdapter-facade  sub-Bus ── claude, gpt, deepseek, ...
           └── Memory-facade ───── sub-Bus ── l1_resident, l2_archive, ...
```

每个 facade Node 是普通 Node，订阅两条 Bus，**自己实现**转发逻辑。

### 1.5.2 框架原语（最小增量）

**不变的部分**（向后兼容）：

- `Bus` 结构体保持单 broadcast 通道模型（CAN 风格）
- `bus.subscribe()` / `bus.connect()` / `NodeHandle::send()` 完全不变
- Engine 内部 ReAct 循环 / 5 Checkpoint / WaitEvent 队列 / Route 解析逻辑不变

**新增 1：`Node` trait 统一抽象**

```rust
// crates/arf-core/src/node.rs

#[async_trait]
pub trait Node: Send + Sync {
    fn id(&self) -> &NodeId;

    /// 序列化自己的状态（2026-06-30 决议：&self）。
    /// snapshot 不阻塞 Node 处理其他消息——Node 内部需用 RwLock/Mutex 保护状态，
    /// Engine 不负责暂停 Node 接收消息。
    /// 返回的 Value 可脱锁后使用；Node 内部应自行处理超时（避免长 lock 卡 barrier）。
    fn snapshot(&self) -> Result<serde_json::Value, SnapshotError>;

    /// 从快照恢复状态（2026-06-30 决议：&mut self）。
    /// restore 期间 Node 不应处理消息（App 负责协调顺序）。
    /// 语义由 Node 决定（Engine 存 State、模型节点存连接池、...）
    async fn restore(&mut self, snapshot: serde_json::Value) -> Result<(), RestoreError>;

    /// 收到消息。`from_bus` 让 Node 知道消息来自哪条 Bus（facade 转发需要）
    async fn on_message(&mut self, msg: Message, from_bus: BusId);
}
```

**Node snapshot 并发约定**（2026-06-30 决议）：
- `snapshot` 是 `&self`——不阻塞 Node 处理消息
- Node 内部用 `RwLock` / `Mutex` 保护共享状态；snapshot 时 read lock，on_message 时 write lock（短临界区）
- Node 内部负责超时控制：建议用 `tokio::time::timeout` 包装 state read 防止卡 barrier
- `restore` 是 `&mut self`——restore 期间 Node 应停止处理消息（App 协调顺序）
- Barrier 调用者不处理 Node 内部超时；Node 实现自负责
- 失败返回 `SnapshotError::Timeout` / `SnapshotError::Serialize` 等；barrier 收到 Err 后该 Node 加入 missing 列表（§1.5.2 Bus::barrier）

**Node 实现示例**：

```rust
impl Node for MemoryNode {
    fn snapshot(&self) -> Result<Value, SnapshotError> {
        // Node 内部负责不超时（防止卡 barrier）
        tokio::time::timeout(Duration::from_secs(2), async {
            let state = self.state.read().await
                .map_err(|e| SnapshotError::Lock(e.to_string()))?;
            serde_json::to_value(&*state)
                .map_err(|e| SnapshotError::Serialize(e.to_string()))
        })
        .map_err(|_| SnapshotError::Timeout)?
    }
}

`NodeId` 全局唯一——同一 Node 在所有订阅的 Bus 上是**同一身份**。

**Message 结构**（Bus 上的 wire format，2026-06-30 增 trigger 字段）：

```rust
/// Bus 上传输的消息。Node 通过 NodeHandle::recv() 收到。
pub struct Message {
    /// 消息类型（路由 key，如 "model_call" / "tool_exec" / "memory_op"）
    pub msg_type: String,
    /// 发送方 NodeId
    pub sender: NodeId,
    /// 接收方 NodeId 列表（Strict 单个；Discovery 多个）
    pub to: Vec<NodeId>,
    /// 消息 payload（ActionMessage::payload() 序列化结果）
    pub payload: serde_json::Value,
    /// 来源 Bus（多 Bus 时由 Bus 写入；单 Bus 时为 Some(bus_id)）
    pub from_bus: Option<BusId>,
    /// Engine 触发上下文（2026-06-30 新增）：
    /// - ReAct 主循环发出的消息（model_call / tool_exec）→ None
    /// - CheckpointRule 触发的消息（memory_op / compact_op / human_handoff 等）→ Some(...)
    /// 用于 trace 区分两类消息；不影响 routing/dispatch 逻辑
    pub trigger: Option<EngineTrigger>,
}

/// Engine 触发 CheckpointRule 时填的元数据
pub struct EngineTrigger {
    /// 触发的 Checkpoint 位置
    pub checkpoint: Checkpoint,
    /// 命中的 CheckpointRule 名称（§2.5 的 name 字段）
    pub rule_name: String,
}
```

**新增 2：Node 可订阅多条 Bus**

```rust
impl NodeHandle {
    /// Node 订阅另一条 Bus。Bus 内部用独立 mpsc channel 接收。
    /// 每条 Bus 订阅有独立 filter（App 在订阅时配置）。
    pub fn attach_to(&mut self, bus: &Bus, filter: MessageFilter) -> &mut Self;

    /// 通过指定 Bus 发消息。Node 必须在该 Bus 上有订阅
    pub async fn send_via(
        &self,
        bus: &Bus,
        to: NodeId,
        payload: serde_json::Value,
    ) -> Result<SendReceipt, SendError>;
}
```

**filter 机制**（2026-06-30 决议）：**接收端过滤**
- Bus broadcast 不做过滤，所有 subscriber 都能收到
- NodeHandle.recv() 按本订阅的 filter 过滤后再返回给 Node
- App 在 `attach_to(bus, filter)` 时为每条 Bus 订阅独立配置 filter
- Node 的 `on_message` 只看到已过滤消息——不再需要内部按 (from_bus, msg_type) 二次匹配
- `from_bus: BusId` 参数保留（用于日志 / 调试 / Node 想区分来源），但不是路由依据

**新增 3（可选）：`Bus::barrier()` 用于全局一致点**

```rust
impl Bus {
    /// 广播 barrier msg，等待指定 Node 集合全部 ack
    /// 返回 BarrierReceipt { acked: Vec<NodeId>, missing: Vec<NodeId> }
    /// 仅供需要全局一致性的 App 使用——纯独立快照的 App 不必调用
    pub async fn barrier(
        &self,
        participants: &[NodeId],
        timeout: Duration,
    ) -> BarrierReceipt;
}
```

**关键约定**：
- `Node::snapshot/restore` 是**必备**（每个 stateful Node 自己实现）
- `Bus::barrier` 是**可选**——只用独立快照的 App 可完全不调
- 框架不强制 checkpoint 策略——App 决定何时 snapshot / 是否用 barrier
- 域控制器 facade 是**App 代码**（普通 Node + 自己的转发逻辑），不是框架原语

### 1.5.3 域控制器作为 App 组合模式

facade Node 是普通 Node，App 自己写转发。

**订阅配置**（filter 在订阅时指定）：
```rust
// 创建 McpFacade，订阅两条 Bus，每条带独立 filter
let mut facade = McpFacade::new(...);
facade.attach_to(bus=top_bus, filter=MessageFilter {
    types: Some(vec!["tool_exec".into()]),     // 只收 top Bus 的 tool_exec
    to_match: ToMatch::DirectedToMe,
});
facade.attach_to(bus=mcp_sub_bus, filter=MessageFilter {
    types: Some(vec!["tool_result".into()]),   // 只收 sub-Bus 的 tool_result
    to_match: ToMatch::DirectedToMe,
});
```

**on_message 只处理已过滤消息**：
```rust
#[async_trait]
impl Node for McpFacade {
    fn id(&self) -> &NodeId { &NODE_ID_MCP }

    async fn on_message(&mut self, msg: Message, _from_bus: BusId) {
        // filter 已过滤，msg 要么是 top 的 tool_exec，要么是 sub 的 tool_result
        match msg.msg_type.as_str() {
            "tool_exec"   => { self.mcp_sub_bus.publish(msg).await; }
            "tool_result" => { self.top_bus.publish(msg).await; }
            _ => unreachable!("filter should have caught this"),
        }
    }

    fn snapshot(&self) -> Result<Value> {
        // facade 是 stateless
        Ok(serde_json::json!({}))
    }
    async fn restore(&mut self, _: Value) -> Result<()> { Ok(()) }
}
```

**简化要点**：filter 在订阅时配置后，`on_message` 不需要检查 `(from_bus, msg_type)` 二元组——filter 已经按 msg_type 过滤，`from_bus` 隐含在订阅上。

**App 拓扑选择**：

| 拓扑 | Engine 订阅 | 适用场景 |
|------|-----------|----------|
| 扁平 | 仅 Top Bus | 简单 App（≤10 个 Node） |
| 域控制器（保守） | 仅 Top Bus，通过 facade | 中等 App（隔离故障域） |
| 域控制器（高效） | Top Bus + 各 sub-Bus | 高复杂度 App（省 facade 转发开销） |

**Engine 在哪种拓扑由 App 决定**——框架不约束。

### 1.5.4 三条不变的不变量

即使加 Multi-Bus，原 Phase 6 三条边界（§10）保持：

1. **Engine 不知道任何具体节点类型** —— `ModelAdapter` / `McpNode` 字眼不出现在 Engine 代码
2. **Node 不知道 Engine 的存在** —— Node 只订阅 msg_types，不假设发送者
3. **Checkpoint 是位置，不是消息类型** —— 5 个位置固定；具体发什么 msg 由 CheckpointRule 决定

Multi-Bus 不引入新边界——`from_bus` 参数让 Node 知道消息来源，但 Node 仍按 msg_type 路由。

## 2. 七大抽象

### 2.1 ActionMessage（trait，可扩展）

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
    /// Engine 不等，receiver 后台自行完成
    Command,
}
```

App 通过实现 `ActionMessage` trait 添加新消息类型（`human_handoff`、`tool_audit`、`progress_update` 等）。

**约定**：内置的几个消息类型——`ModelCall`、`ToolExec`、`MemoryOp`、`CompactOp`——都是 trait 的具体实现，**不是封闭枚举**。

**内置 msg_type 白名单**（Engine 隐式处理，无需 register_processor）：

```rust
// crates/arf-core/src/message.rs

/// Engine 隐式处理的内置 msg_type 及其响应映射。
/// WaitEvent 收集到响应时按此表 dispatch；未列出的 msg_type 必须通过
/// AgentConfig.processors 注册 ResponseProcessor（§5.4）。
pub const BUILTIN_MSG_TYPES: &[(&str, &str)] = &[
    ("model_call",  "model_response"),  // ModelAdapter 处理 → engine 注入 assistant 消息
    ("tool_exec",   "tool_result"),     // McpNode 处理 → engine 注入 tool 消息
];
```

其他所有 msg_type（如 `memory_op` / `compact_op` / `human_handoff` / `subagent_op` 等）的响应处理都必须通过 `AgentConfig.processors: HashMap<String, Arc<dyn ResponseProcessor>>` 注册，Engine 在 WaitEvent 完成时按响应 msg_type 查表 dispatch。

### 2.2 Response（单一形态）

```rust
pub enum Response {
    /// Receiver 给出的最终值。
    /// Engine park 等所有 Query 接收方都给出 Done(Value) 才继续。
    /// Value 的语义由 msg_type 决定：
    ///   - model_call → { content: String, tool_calls: Vec<ToolCall> }
    ///   - tool_exec → { content: String, error?: String }（error 字段为业务错误，由 App 解释）
    ///   - 自定义类型 → 由 AgentConfig.processors 解释（§5.4）
    /// Engine 不解析 Value 内部字段，不区分成功/业务错误——所有返回都视为"Done"。
    Done(serde_json::Value),
}
```

**无 `Failed` / `Err` / `Timeout` 变体**。理由（2026-06-30 决议）：
- **Engine 不解析 Receiver 内部错误**——Receiver 内部错误处理是 App 开发者在执行节点（如 ModelAdapter / McpNode）中的职责。Engine 只确保"信息发出 + 信息接收"，对 Value 内部字段无任何语义假设
- **Receiver 崩溃表现为两种 Engine 可观察的信号**（§5.7）：
  - `node_offline` lifecycle signal（进程死了）→ OnMemberFailedHandler 处理
  - 超时（hang 住但未下线）→ OnMemberFailedHandler 处理
- **业务错误**（如模型返回 "I cannot do that"、tool 抛 PermissionError）作为 Value 内容正常返回，由对应 processor 解释；Engine 不需要 `Failed` 变体

**无 `Wait`**。理由：
- intent=Query → Engine 必然等所有 receiver，无论快慢
- intent=Command → Engine 不等，receiver 返不返都不影响
- Receiver 慢就慢着，Engine 已经 park；不需要多余 ack 协议
- Receiver 存活由 Heartbeat 机制覆盖，不污染 Response 协议

### 2.3 Route（二元）

```rust
pub enum Route {
    /// 严格模式：发向指定的 node_id(s)
    Strict(Vec<NodeId>),

    /// 发现模式：发向 BusGraph 中声明了该 capability 的所有节点
    Discovery(Capability),
}
```

**Discovery + Query 语义**：Engine 等所有匹配节点最终响应。
**Discovery + Command 语义**：Engine 不等，所有匹配节点收到后自行处理。

无 `Any` 模式——必须显式声明 capability 才能 Discovery。

#### 2.3.1 Capability 匹配机制

**Node 声明能力**（connect 时一次性声明）：

```rust
// McpNode 注册时声明
let mcp_info = NodeInfo {
    node_id: NodeId::new(id="local_mcp"),
    capabilities: serde_json::json!({
        "kind": "mcp",
        "transport": "stdio",
        "tools": ["read_file", "bash", "edit_file"],
    }),
    ...
};
bus.connect(info=mcp_info, filter=mcp_filter).await?;

// ModelAdapter 注册时声明
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

**Capability 定义**（多对 key-value，AND 语义）：

```rust
pub struct Capability {
    /// 全部满足才算匹配（AND 语义）
    pub requirements: Vec<(String, String)>,
}
```

**Engine 解析 Discovery Route**：

```
Route::Discovery(capability=cap) 触发解析
       │
       ▼
遍历 BusGraph.nodes（在线节点列表）
       │
       ▼
对每个 NodeInfo：
    node.capabilities 是否包含 cap.requirements 的所有 key-value？
       │
       ├─ yes → 加入 receivers 列表
       └─ no  → skip
       │
       ▼
receivers 列表（可能为空，可能多个）
       │
       ▼
按 receivers 投递消息
```

**匹配示例**：

```rust
// 匹配任何 MCP 节点
Capability { requirements: vec![("kind".into(), "mcp".into())] }
// 命中：local_mcp

// 匹配 primary 层的 model
Capability { requirements: vec![
    ("kind".into(), "model".into()),
    ("tier".into(), "primary".into()),
]}
// 命中：NodeInfo.capabilities.kind == "model" && tier == "primary"

// 匹配 file-backed memory
Capability { requirements: vec![
    ("kind".into(), "memory".into()),
    ("backend".into(), "file".into()),
]}
```

**关键约定**：
- `requirements` 全部满足才匹配（**AND 语义**；需要 OR 时拆成多次 Discovery）
- `NodeInfo.capabilities` 是 JSON Value，可以是字符串、数字、数组、对象——Capability 匹配只看**顶层字符串字段**；数组/嵌套对象不进 match
- **App 不要把路由关键信息放在数组里**——数组字段只能用于 Node 信息展示（如 `tools: ["read_file", "bash"]` 仅给人看，不能用于 Discovery 匹配）
- Capabilities 在 connect 时声明，**不变直到 disconnect + reconnect**；运行时变更需重连
- Engine 缓存解析结果，收到 `node_online` / `node_offline` lifecycle signal 时失效缓存

**边界场景**：
- 无匹配 → Engine 抛 `NoReceiver` 错误，App 决定 fail-fast / 降级 / 重试
- 单匹配 → 退化为 Strict 单 receiver 行为
- 多匹配 → 按 Discovery 语义（Query 等全部，Command 全发）

### 2.4 State（messages + over_view + wait_events）

**所有权语义**（2026-06-30 决议）：
- **App 持有 State**（在 `engine.run()` 之外；State 的生命周期由 App 管理）
- **Engine.run(&mut state, ...)** 期间借走 &mut State，修改 messages / over_view / wait_events
- Engine 不再拥有 State——State 的持久化、克隆、跨 Engine 共享都是 App 决定
- App **不**应该直接访问 `state.wait_events`（Engine 内部维护）；仅 `messages` 和 `over_view` 是 App 可读的"对话视图"

```rust
pub struct State {
    /// 对话历史（详细）。Engine 在 chat() 中追加；App 可读。
    pub messages: Vec<ModelMessage>,
    /// 聚合指标（O(1) 访问）。Engine 在每次 chat() / ReAct 转移点维护；App 可读。
    pub over_view: OverView,
    /// 等待中的消息组（§5）。Engine 内部维护；App 不应访问。
    /// 包含所有未完成 WaitEvent，snapshot 时随 State 一起序列化（§5.5）。
    pub wait_events: Vec<WaitEvent>,
}

/// ModelMessage — State 使用的领域层消息类型。
/// 与 Bus 上的 `Message`（wire format，含 from_bus/to/sender/payload）区分：
/// ModelMessage 是 domain format（role/content/tool_calls），仅在 State 内与 LLM 交互时使用。
/// Engine 注入 State 时由 App 注册的 ResponseProcessor 把 wire 响应转为 ModelMessage。
pub struct ModelMessage {
    pub role: Role,
    pub content: String,
    /// assistant 消息包含 tool_calls 时填充
    pub tool_calls: Vec<ToolCall>,
    /// tool 响应消息对应的主调用 id（role=Tool 时填充）
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

// WaitEvent 队列（§5）是 State 的逻辑扩展——
// 它属于 Engine 的运行时状态，跟随 State 一起持久化（§5.5）

pub struct OverView {
    pub round_count: usize,
    pub turn_count: usize,
    pub context_tokens: usize,
    pub model_context_window: usize,
    pub runtime: Duration,
    pub last_user_message: String,
}
```

**设计变更**（2026-06-30）：移除原 `tasks: Vec<Task>` 字段。理由：
- 所有 in-flight 操作已被 WaitEvent 覆盖（§5）
- 持久化已被 §5.5 WaitEvent 序列化解决
- 框架不应假设 UI 层"查询运行中任务"的需求
- "Don't add abstractions beyond what the task requires"

`over_view` 字段由 Engine 在每个 Checkpoint 转移点维护。`messages` 是底层详细数据，Checkpoint 条件复杂时可自行遍历。

**字段计算策略**：

| 字段 | 计算方式 | 时机 |
|------|---------|------|
| `round_count` | `state.messages` 中 `role=user` 的条数（或 Engine 内部计数器） | 每次 chat() +1 |
| `turn_count` | Engine 内部计数器；每发一次 model_call/tool_exec +1 | ReAct 转移点 |
| `context_tokens` | **API usage 捕获**：从 model_call 响应的 `usage.prompt_tokens` 取 | 每次 model_call 响应后写入 |
| `model_context_window` | 启动时从 ModelAdapter 的 capabilities 读取；AgentConfig 可覆盖 | EngineBuilder.build() 时 |
| `runtime` | Active time（仅 Engine 处于 `processing` 状态的累计时长，不含 `waiting` / `stopped`） | 状态机转移点累加 |
| `last_user_message` | 最近一次 chat() 的 user_input | chat() 时更新 |

**`context_tokens` 的精确性来源**：
- 每次 model_call 响应都带 `usage.prompt_tokens`（OpenAI / Anthropic / DeepSeek 等都返回）
- Engine 不解析 message 字节、不做 char/word 启发式估算
- 唯一一次估算在 session 初始（messages 刚 push 后）；之后每次 model_call 自动校准
- CheckpointRule 触发的 CompactOp / MemoryOp 等会修改 messages，造成少量漂移；下次 model_call 响应的 usage 会重新精确

### 2.5 CheckpointRule（四元组）

```rust
pub enum Checkpoint {
    /// model_call 触发前
    BeforeModelCall,
    /// model_call 完成后
    AfterModelCall,
    /// tool_exec 触发前
    BeforeToolExec,
    /// tool_exec 完成后
    AfterToolExec,
    /// round 边界，准备发 final_output
    RoundEnd,
}
```

**约定**：
- Engine 在固定 5 个 Checkpoint 位置暂停
- 调用所有注册规则的 `when`，返回 `true` 才调用 `build`
- Engine 从 `build(state)` 返回的 msg 取 `msg_type()`，按 `AgentConfig.routes[msg_type]` 投递（**Route 单一源**）
- Engine 按 `msg.intent()` 决定 park 等响应（Query）还是 fire-and-forget（Command）
- 框架提供标准构造器（`every_n_rounds`、`when_context_over`），但底层都是四元组

**CheckpointRule 四元组定义**（name + trigger + when + build）：

```rust
pub struct CheckpointRule {
    /// 规则的唯一名称（用于日志 / 调试 / 禁则）
    pub name: String,
    /// 触发位置（5 个 Checkpoint 之一）
    pub trigger: Checkpoint,
    /// 条件谓词：返回 true 才执行 build
    pub when: Box<dyn for<'a> Fn(&'a State) -> bool + Send + Sync>,
    /// 构造 ActionMessage：从 State 生成要发送的消息
    pub build: Box<dyn for<'a> Fn(&'a State) -> Box<dyn ActionMessage> + Send + Sync + 'a>,
}

impl CheckpointRule {
    /// 位置参数构造器。闭包参数接受 `for<'a> Fn(&'a State) -> ...`，避免与 struct field 生命周期不一致。
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

**Engine 在 trigger 处的执行逻辑**（伪代码）：

```rust
for rule in &self.checkpoint_rules {
    if rule.trigger != trigger { continue; }
    if !(rule.when)(state) { continue; }

    let msg: Box<dyn ActionMessage> = (rule.build)(state);
    let route = self.config.routes.get(msg.msg_type())
        .ok_or(EngineError::MissingRoute(msg.msg_type().into()))?;

    // 2026-06-30 决议：CheckpointRule 触发的消息带 trigger 上下文
    // 让 trace 能区分 "LLM 主循环消息" vs "Engine 规则触发消息"
    let trigger_meta = EngineTrigger {
        checkpoint: rule.trigger,
        rule_name: rule.name.clone(),
    };
    self.bus.publish(msg=msg.as_ref(), route=route, trigger=Some(trigger_meta)).await?;

    match msg.intent() {
        MessageIntent::Query => {
            // 创建或加入 WaitEvent，等待 receiver 响应
            self.wait_events.add(msg).await;
        }
        MessageIntent::Command => {
            // fire-and-forget：不加入 WaitEvent
        }
    }
}
```

**Route 单一源约定**（2026-06-30 决议）：`AgentConfig.routes` 是 Engine 投递消息的唯一依据——既用于 ReAct 循环主动发的 ModelCall/ToolExec，也用于 CheckpointRule.build 产生的所有 msg。`CheckpointRule` 不携带 route（避免双源不一致）。

**Intent 单源约定**（2026-06-30 决议）：`intent` 来自 `ActionMessage::intent()` trait 方法，由具体 msg 类型（如 MemoryOp、CompactOp）声明。`CheckpointRule` 不显式声明 intent（避免与 msg.intent() 不一致）。

**不在 Checkpoint 范畴**：
- **Input 处理**（user_input 进入 state）发生在 `engine.run(state, user_input)` 调用方，App 在外层拦截/转换/审批；Engine 内部不设 BeforeInput/AfterInput
- **System prompt 组装 + Tools/Skills 收集**发生在 `EngineBuilder.build()` 时（见下方 §2.5.1）
- **运行时 memory 抽取**：CheckpointRule.build 返回 MemoryOp::extract 作为 Command msg，发到 MemoryNode（详见 §11.1）
- **运行时 memory 检索**：由模型主动发起 tool call（如 `memory_search`），走标准 tool_exec 流程；**不**是单独的 MemoryOp::Retrieve msg
- **Turn 结束**与 RoundEnd 合并（每 turn 必然在 round 内；如需 turn 级 hook 用 `Checkpoint::RoundEnd` + `over_view.turn_count` 判断）

#### 2.5.1 Engine build() 一次性组装（2026-06-30 决议）

`EngineBuilder.build(config)` 内部顺序执行：

1. **聚合多 Bus 视图**：EngineBuilder 接 `buses: Vec<Arc<Bus>>`，build() 遍历所有 Bus 的 graph，union 为 `merged_graph: Vec<NodeInfo>`（**Bus 上节点先去重**：NodeId 全局唯一，同一 Node 在多条 Bus 上只出现一次，详见 §1.5.2）
2. **校验 routes**：检查 `AgentConfig.routes` 中所有 msg_type 对应的 Node（Strict → 精确 NodeId；Discovery → Capability 匹配）都在 `merged_graph` 上线（fail-fast）
3. **过滤并收集 Skills**：遍历 `merged_graph` 中所有 `kind=skill` 的 Node，过滤 `config.skills_include / skills_exclude`（glob 模式），将命中的 skill 描述填到 `system_prompt_template` 的 `{{skills}}` 占位符
4. **过滤并收集 Tools**：遍历 `merged_graph` 中所有 `kind=mcp` 的 Node，过滤 `config.tools_include / tools_exclude`，将命中的 tools 存为 `Engine.tools: Vec<ToolSpec>`（**不**写入 prompt，见下方）
5. **格式化 system prompt**：用填好 skills 的 prompt 生成 `messages[0]`（system role）
6. **追加 initial_memory**：将 `config.initial_memory: Vec<String>` 依次转为 system role messages，追加到 `messages[1..]`（保持最长前缀稳定）
7. **创建 Engine**：Engine 持有 (formatted_messages + initial_memory) 作为初始 state.messages

**资源过滤 glob 语法**（2026-06-30 决议）：
- 工具/技能的完整标识符为 `{node_id}.{tool_name}`（如 `mcp_local.read_file`、`skill_hub.greet`）
- include 模式匹配 glob：
  - `exact_name` — 精确匹配（如 `read_file` 匹配任何 node 上的 `read_file`）
  - `node_id.*` — 匹配该 node 上的所有工具（如 `mcp_local.*`）
  - `*:read_*` — 匹配任何 node 上以 `read_` 开头的工具
  - `mcp_*` — 匹配任何以 `mcp_` 开头的 node
- exclude 模式语法同上；**excluded 优先于 included**（先 exclude，再 include）
- include 为空列表 → 不收任何该类资源；include 为 None 或包含 `*` → 收全部（除非被 exclude）

**Tools 不入 prompt**（2026-06-30 决议）：
- LLM API（OpenAI/Anthropic/DeepSeek）原生支持 `tools` 参数，独立于 system prompt
- Engine 在构造 `ModelCall` 时将 `Engine.tools` 装入 payload（不是 state.messages 的一部分）
- App 的 `system_prompt_template` 中**不应**包含 `{{tools}}` 占位符（如果写了，Engine 不替换）

**多 Bus 视图聚合**（2026-06-30 决议）：
- EngineBuilder 现在接 `buses: Vec<Arc<Bus>>`，不是单 Bus
- build() 把所有 Bus 的 graph union 为 merged_graph（NodeId 去重）
- routes 校验、tools/skills 收集都走 merged_graph
- App 显式声明 Engine 订阅哪些 Bus（与 §1.5 Node trait 的 `attach_to` 配合）

**Memory 注入语义**（2026-06-30 决议）：
- **固定 memory（build 时）**：`config.initial_memory` 追加到 messages 前缀，作为 system role message
- **运行时 retrieval**：由模型主动调 `memory_search` 等 tool，**不**触发 MemoryOp::Retrieve msg
- **运行时 extraction**：CheckpointRule 触发 `MemoryOp::extract` Command，MemoryNode 完成后**不**修改 messages（memory 抽取是后台操作，模型看不到抽取过程）；抽取结果在下次 session 加载时作为 initial_memory 出现
- **前缀稳定原则**：所有 build 时一次性确定的 memory 放在 messages **前部**；会话过程中产生的 system messages（如人工补充）追加到 messages **尾部**——确保前缀 cache 命中率

```rust
// ModelCall payload 包含 tools（不进 messages）
pub struct ModelCall {
    pub messages: Vec<ModelMessage>,
    pub tools: Vec<ToolSpec>,
    // ...
}

// ModelAdapter 调用 LLM API：
let req = CreateRequest {
    messages: call.messages,
    tools: call.tools,  // OpenAI/Anthropic 原生支持
    // ...
};
```

**ToolSpec 定义**（App 实现工具时遵循）：

```rust
/// 工具规范。ModelAdapter 在 ModelCall 时传给 LLM API。
/// App 在 McpNode connect 时声明；Engine build() 时收集到 Engine.tools。
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,  // JSON Schema
}
```

### 2.6 Engine

Engine 是 Bus 上一个特殊 Node，运行固定 ReAct 状态机。

**Engine 拥有**：
- ReAct 状态机（idle / processing / waiting / stopped，详见 §4.1）
- 5 个 Checkpoint 触发位置
- Park/Resume 机制（&mut State 期间维护 wait_events 字段）

**Engine 不拥有**：
- State 所有权（State 由 App 持有；Engine.run() 借用 &mut State，详见 §2.4）
- 任何具体 Node 实例（ModelAdapter / McpNode / MemoryNode 等字眼不出现在 Engine 代码）
- Route 表
- CheckpointRule 列表
- messages/tasks 的具体业务解释（Engine 只做存取）
- 持久化时机与存储后端（App 全权）

**Engine API**（2026-06-30 决议：去掉 Session 抽象）：

```rust
impl Engine {
    /// 跑一轮 chat：处理 user_input，触发 ReAct 主循环，最终修改 state 并返回 final_output。
    /// App 调用方负责：input 拦截、State 持久化、生命周期管理。
    /// Engine 不感知 Session 概念——App 自由组合 Engine + State + 持久化层。
    ///
    /// `cancel` 参数：App 通过 CancellationToken 在任意点取消 run()。
    /// 取消时 Engine 给所有 in-flight Receiver 发 `cancel` msg（§G6），
    /// 清空 state.wait_events，返回 Err(Stopped)。
    pub async fn run(
        &self,
        state: &mut State,
        user_input: String,
        cancel: CancellationToken,
    ) -> Result<String, RunError>;

    /// 同步快照当前 state（与 Node::snapshot 风格一致）。
    /// App 可同步组合 Session（保存快照 / 序列化到磁盘 / 中断对话）。
    pub fn snapshot(&self, state: &State) -> SessionSnapshot;

    /// 从快照恢复 state（异步，因可能涉及 Node 重连等异步操作）。
    /// App 控制何时 resume、何时持久化。
    pub async fn restore(&self, snap: SessionSnapshot) -> Result<State, RestoreError>;
}

/// 取消令牌。App 在调用 engine.run() 前 clone() 一份留作"远程控制器"。
/// 跨线程安全；任一处调用 .cancel() 即触发 Engine 停止。
pub type CancellationToken = tokio_util::sync::CancellationToken;

/// State 序列化快照（含 messages + over_view + WaitEvent 列表）。
/// App 决定存哪里（文件 / DB / S3）、何时存、怎么存。
#[derive(Serialize, Deserialize)]
pub struct SessionSnapshot {
    pub version: u32,         // schema 版本
    pub state: State,
    pub wait_events: Vec<WaitEvent>,
    pub created_at: i64,
    pub metadata: HashMap<String, Value>,  // App 自定义元数据
}
```

**为什么没有 Session struct**（2026-06-30 决议）：
- Session 本质是 App 对话层抽象，可能跨多个 Engine（agent group）
- 用户视角只是"我开始了一段对话"，不知道背后 Engine 拓扑
- 框架提供原语（Engine.run + snapshot + restore），App 自由组合：
  - 简单 App：1 Engine + 1 State + 文件持久化
  - 复杂 App：N Engine（agent group）+ 跨 Engine 共享 State + 数据库持久化
- 框架不做 `Session` / `start_session` / `session.chat` 等抽象，避免与 App 自定义 Session 冲突
- **App 用 §1.5.2 已有的 `Node::snapshot/restore` 同步机制组装 Session**——单 Engine 持久化只需 Engine.snapshot(state)；多 Engine 持久化用 §5.6 `Bus::barrier` 协调后再持久化

### 2.7 App（唯一的装配者）

App 通过 `EngineBuilder` 把所有部件组装起来。Engine 不参与装配。

## 3. App 装配模型

**2026-06-30 修订**：移除 `NodeBinding`（双重声明，违反 DRY）。`AgentConfig` 只声明路由和 CheckpointRule；具体 Node 通过 `bus.connect()` 上线，`build()` 时 fail-fast 校验 routes。

```rust
// crates/arf-agent/src/builder.rs

// ── 1. 创建 Bus（可多个）───────────────────────────────────────
let bus_top = Bus::new(heartbeat_interval=5000, heartbeat_timeout=15000, channel_capacity=64);
let bus_sub = Bus::new(heartbeat_interval=5000, heartbeat_timeout=15000, channel_capacity=64);
// EngineBuilder.build() 聚合所有 Bus 的 graph（NodeId 去重）

// ── 2. 创建并连接具体 Node（必须在 build() 之前！）───────────────
//    build() 时 fail-fast 校验 routes → 查 merged_graph，
//    若 node_id 不在线或 capability 无匹配，返回 BuildError。
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

// 子 Bus 上的特殊 mcp（facade 不参与——本例假设 sub_bus 上直接挂节点）
let mcp_advanced = McpNode::new(
    node_id=NodeId::new(id="advanced_mcp"),
    config=advanced_config,
);
bus_sub.connect(info=mcp_advanced.info(), filter=mcp_advanced.filter()).await?;

let memory = MemoryNode::new(
    node_id=NodeId::new(id="memory_node"),
);
bus_top.connect(info=memory.info(), filter=memory.filter()).await?;

// ── 3. 声明 AgentConfig（routes + checkpoint_rules）──────────────
let config = AgentConfig {
    agent_id: "assistant".into(),
    model_config: ModelConfig { provider: "deepseek".into(), model: "deepseek-v4-flash".into() },
    // 注意：tools **不**在这里。{{skills}} 占位符由 Engine build() 时查 BusGraph 填。
    system_prompt_template: "You are a helpful assistant.\n\nSkills:\n{{skills}}".into(),
    // 固定 memory：build() 后作为 system 消息追加到 messages 前缀（cache 命中稳定）
    initial_memory: vec![
        "User is a financial analyst working on quarterly reports.".into(),
        "Previous context: user prefers concise summaries with tables.".into(),
    ],
    max_turns: 10,
    routes: {
        let mut m = HashMap::new();
        m.insert("model_call".into(), Route::Strict(vec![NodeId::new(id="primary_model")]));
        m.insert("tool_exec".into(), Route::Discovery(Capability::new(key="kind", value="mcp")));
        m.insert("memory_op".into(), Route::Strict(vec![NodeId::new(id="memory_node")]));
        m.insert("compact_op".into(), Route::Discovery(Capability::new(key="kind", value="compactor")));
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
    // 资源过滤（§2.5.1）：只收 local_mcp 的基础工具 + advanced_mcp 的所有工具
    tools_include: Some(vec![
        "local_mcp.read_file".into(),
        "local_mcp.bash".into(),
        "local_mcp.edit_file".into(),
        "advanced_mcp.*".into(),  // 通配：该 node 上所有工具
    ]),
    tools_exclude: vec![
        "advanced_mcp.dangerous_exec".into(),
    ],
    skills_include: Some(vec![
        "skill_hub.greet".into(),
    ]),
    skills_exclude: vec![],
    ..Default::default()
};

// ── 4. build() fail-fast 校验 ──────────────────────────────────
//   * Strict route 的 NodeId 必须当前在线
//   * Discovery route 的 Capability 必须当前有匹配
//   * 失败 → BuildError { missing_nodes, missing_capabilities }
let engine = EngineBuilder::new(buses=vec![bus_top.clone(), bus_sub.clone()])
    .build(config=config)
    .await?;

// Engine 也作为 Node 上 Bus（§1.5 Node trait）
bus.connect(info=engine.info(), filter=engine.filter()).await?;

// ── 5. 运行（2026-06-30 决议：框架不提供 Session 抽象）──
//    App 自己持有 state 并管理生命周期：
let mut state: State = State::default();
let cancel = CancellationToken::new();
let output = engine.run(state=&mut state, user_input="Read /etc/hostname".into(), cancel=cancel.clone()).await?;
//    App 决定何时快照：engine.snapshot(&state) 是同步的，可直接存到文件/DB。
let snap: SessionSnapshot = engine.snapshot(state=&state);
//    cancel.cancel() 可在任意点触发 Engine 停止（§G6）
serde_json::to_writer(File::create("session.json")?, &snap)?;
```

**为什么去掉 `NodeBinding`？**
- `NodeBinding` 跟实际 Node 的 `bus.connect(info, filter)` 重复声明 NodeId 和 subscriptions
- Engine 不需要"哪些 Node 存在"——BusGraph 知道
- Engine 不需要"Node 订阅什么"——Bus filter 知道
- Engine 只需要"哪些 msg_type 走哪条 Route"——`AgentConfig.routes` 已声明
- `build()` 校验 routes 时直接查 BusGraph，fail-fast 给出具体缺失项

**校验失败示例**：
```rust
// 假设 App 忘记实例化 "primary_model"
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

## 4. ReAct 循环（固定，不可配置）

```
engine.run(state=&mut state, user_input=msg)  ← App 端：拦截/转换/审批在调用方完成
       │
       ▼
   state.messages.push(value=msg)
       │
       ▼
   ┌─ Checkpoint::BeforeModelCall ─┐
   │ for rule in rules:            │
   │   if rule.when(state):        │
   │     msg = rule.build(state)   │
   │     bus.publish(msg=msg, route=route)   │
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

**关键约定**：
- App 调 `engine.run(state=&mut state, user_input=msg)` 之前负责 input 拦截（验证、转换、审批），Engine 不感知
- Engine 内部 ReAct 循环只围绕 `model_call` 和 `tool_exec` 两个 action
- 5 个 Checkpoint 是位置标记；具体发什么 msg 由 CheckpointRule 决定
- Engine 只通过 `ModelCall` / `ToolExec` 两个内置消息类型与 Bus 通信；`Memory` / `Compact` / `Subagent` / `HumanHandoff` 等由 App 通过 CheckpointRule 注入

### 4.1 四状态

| 状态 | 含义 |
|------|------|
| `idle` | `state.wait_events.is_empty()`，无 in-flight 操作 |
| `processing` | 正在执行 ReAct 循环主流程（构造 msg / publish / 等响应 / 更新 State） |
| `waiting` | `!state.wait_events.is_empty()`，等待所有未完成 WaitEvent 完成 |
| `stopped` | 收到 stop 信号，run() 返回 `Err(EngineError::Stopped)` |

**术语约定**：状态机名称为 `waiting`；"park" 是动词（"Engine 进入 waiting 状态 / park 等响应"），不是状态名。

**状态机转移矩阵**（2026-06-30 补全）：

| From → To | 触发条件 | Engine 副作用 |
|-----------|----------|--------------|
| `idle` → `processing` | `engine.run(state, user_input)` 被调用 | state.messages.push(user_msg); state.over_view.round_count += 1; 触发 BeforeModelCall Checkpoint |
| `processing` → `processing` | ModelCall 响应到达，需要 tool_exec | 追加 assistant ModelMessage；触发 AfterModelCall Checkpoint；触发 BeforeToolExec；publish tool_exec |
| `processing` → `waiting` | Engine publish 一个 intent=Query 的 msg | state.wait_events.push(new_event); 等待 receiver 响应 |
| `processing` → `waiting` | Engine publish 多个 intent=Query msg（multi-member event） | state.wait_events.push(multi_member_event); 等待所有 member 响应 |
| `waiting` → `processing` | 某 WaitEvent 的 strategy 满足（All/Any/Count(n)） | 从 state.wait_events 移除；inject responses to state.messages；触发新一轮 think |
| `waiting` → `waiting` | 部分 member 响应到达但未满足 strategy | 更新对应 member.received_count；继续 park |
| `waiting` → `stopped` | App 调用 `cancel.cancel()`（§G6） | 给所有 in-flight Receiver 发 `cancel` msg；state.wait_events 清空；run() 返回 Err(Stopped) |
| `processing` → `stopped` | 命中终止条件（§4.2）：纯文本 / task_complete / max_turns / 不可恢复错误 | run() 返回 Ok(final_output) 或 Err(error) |
| `waiting` → `stopped` | node_offline lifecycle signal + OnMemberFailedHandler 返回 FailSession | state.wait_events 清空；run() 返回 Err |
| `*` → `idle` | （仅在 run() 返回后发生；下次 run() 进来再决定转 processing 还是 stopped） | state 不变 |

**关键不变式**：
- `idle` ↔ `waiting` 通过 `processing` 中转（不能直接互转）
- `stopped` 是终态；后续 `engine.run()` 需 App 显式重置（如 `engine.restore(snap)`）
- 进入 `stopped` 后，state.wait_events 必清空（持久化前）
- `processing` 不会"卡住"——任何 publish 出去的 Query intent msg 都会立即让状态转 `waiting`

### 4.2 终止条件

| 条件 | 触发 | 转移目标 |
|------|------|----------|
| 模型返回纯文本 | LLM 不再调用 tool | `processing` → `stopped`（run 返回 Ok(output)） |
| `task_complete` | LLM 调用了 kernel tool | `processing` → `stopped`（run 返回 Ok(output)） |
| `max_turns` 超限 | `turn_count >= max_turns` | `processing` → `stopped`（run 返回 Err(MaxTurnsExceeded)） |
| cancel | App 调 `cancel.cancel()` | `*` → `stopped`（run 返回 Err(Stopped)） |
| 不可恢复 error | Receiver panic / OnMemberFailed 返回 FailSession / 节点全掉 | `*` → `stopped`（run 返回 Err(EngineError)） |

## 5. 等待队列（事件级）

Wait list 的基本单元是 **WaitEvent**，不是单个消息。一个 event 可以等待**多条消息**（members），所有 member 满足触发策略后整体出栈，触发新一轮 think。

```rust
pub struct WaitEvent {
    id: EventId,
    /// 该 event 等待的所有消息
    members: Vec<PendingMessageWait>,
    /// 已收集的响应（按 correlation_id 索引）
    received: HashMap<CorrelationId, Response>,
    /// 触发策略
    strategy: WaitStrategy,
    created_at: Instant,
}

pub enum WaitStrategy {
    /// 所有 member 都响应才触发（默认）
    All,
    /// 任一 member 响应就触发，其余响应被丢弃
    Any,
    /// 指定数量 member 响应后触发
    Count(usize),
}

pub struct PendingMessageWait {
    correlation_id: CorrelationId,
    msg_type: String,
    message_payload: Value,     // 完整原始消息，Bus 重启后重发
    expected_receivers: usize,  // Strict 时=1；Discovery 时=BusGraph 匹配数
    received_count: usize,
}
```

### 5.1 默认行为：1 send = 1-member event

每次 `send(msg, route)` 自动创建一个 1-member event，strategy=All。

```rust
let corr = engine.send(msg=model_call, route=Route::Strict(vec![primary_model])).await?;
// 隐式创建 WaitEvent { strategy=All, members=[{corr}] }
// 等到所有 receiver 都响应后，event 出栈
```

### 5.2 App 显式：多消息合并为一个 event

```rust
// 创建一个 multi-member event
let event = engine.create_wait_event(strategy=WaitStrategy::All);

// 两条消息绑定到同一 event
let corr1 = engine.send_to_event(msg=model_call, route=Route::Strict(vec![primary_model]), event_id=event.id);
let corr2 = engine.send_to_event(msg=tool_exec, route=Route::Discovery(Capability::new(key="kind", value="mcp")), event_id=event.id);

// 等待 event 完成（等到 model_call 和 tool_exec 都响应）
let responses = engine.await_event(event_id=event.id).await?;
// responses = [(corr1, model_response), (corr2, tool_response)]

// Engine 把收集到的响应注入 State，触发新一轮 think-decide-react
```

**典型场景**：
- **多模型投票 + 工具执行并行**：Strict([claude, gpt]) 的 model_call + Discovery 的 tool_exec，**等所有结果都齐**再做下一轮 think
- **Human-in-the-loop + 后台任务**：HumanHandoff(Query) + ProgressUpdate(Command)，等用户响应 + 后台完成**都齐**才继续
- **First-responder 路由**：strategy=Any 时，Strict([claude, gpt]) 中**任一**模型先响应就触发 event，丢弃另一模型的响应

### 5.3 生命周期

```
Engine send(msg, route, event?)
       │
       ├─ event=None → 自动创建 1-member WaitEvent, 入队列
       └─ event=Some(id) → 添加到 event.members
       │
       ▼
Response 到达
       │
       ▼
更新对应 member.received_count
       │
       ▼
event.strategy 满足?
       ├─ All  → 所有 member 都 received_count == expected_receivers
       ├─ Any  → 任一 member received_count == expected_receivers
       └─ Count(n) → 至少 n 个 member 完成
       │
       ├─ yes → 收集所有 responses → 移出队列
       │         Engine 触发新一轮 think-decide-react
       │
       └─ no  → 留在队列继续等

Cancel 到 → 通知 event 所有未响应 receiver, 移出队列
Bus 重启 → 取所有未完成 event 的 member.message_payload, 重新 publish
```

**Cancel 传播流程**（2026-06-30 补全 §G6）：

```
App 调 token.cancel()（任意线程）
       │
       ▼
Engine.run() 内 select! 分支触发
       │
       ▼
遍历 state.wait_events 中所有未完成 event
       │
       ├─ 对每个未响应 member 发 cancel msg：
       │     msg_type = 'cancel'
       │     to = member.target_node_id
       │     payload = { correlation_id: member.correlation_id }
       │     // Receiver 内部决定是否中断处理
       │     // 如 ModelAdapter 看到 cancel 后中断 reqwest 调用
       │
       ├─ 清空 state.wait_events（持久化前的已取消 event 不需保留）
       │
       └─ run() 返回 Err(RunError::Stopped)
              │
              ▼
       App 拿到 Err，决定下一步：
         - 持久化当前 state（含 cancel 后的部分消息）
         - 调用 engine.cancel_token.cancel() 触发
         - 重新发起新 run()（state 已变更）
```

**为什么发 cancel msg 而不是直接丢弃**（2026-06-30 决议）：
- Receiver 可能正在执行长任务（Model API 30 秒调用、McpNode 工具执行）
- 直接丢弃 Response 会让 Receiver 浪费计算资源
- 发 cancel msg 让 Receiver 主动中断：避免资源浪费
- Receiver 是否处理 cancel 是它的实现细节——简单 Receiver 忽略 cancel 也不影响正确性（Engine 已不 park 等响应）

**cancel msg 处理约定**：
- `cancel` 是新的 msg_type——App 实现 Receiver 时可选地订阅
- 不强制所有 Receiver 处理 cancel（内置消息类型按需实现）
- Receiver 收到 cancel 后的行为由 App 决定（如中断 reqwest 调用、关闭子进程等）

**框架内置 Receiver 必须同步响应 cancel**（2026-06-30 决议）：

| Receiver | cancel 处理细节 |
|----------|-----------------|
| `ModelAdapter` | 立即中断正在进行的 HTTP 请求（`reqwest` 调用 abort handle）——避免继续占用 LLM API 计算配额 |
| `McpNode` | 中断工具执行（subprocess kill、reqwest abort）——避免长任务（如 `sleep 60`）继续消耗 CPU/IO |

**约定**：
- 框架内置 Receiver **必须**实现 cancel 响应（cancel 触发即停止资源占用）
- App 自定义 Receiver 也应实现（避免资源浪费）
- Receiver 收到 cancel 后不必返回 Response——Engine 已不 park 等响应
- 如果 Receiver 不响应 cancel，Engine 仍正确（run 返回 Err(Stopped)），但**资源被浪费**

### 5.4 Event 触发的 think-decide-react

当 event 出栈时，Engine 顺序执行：

1. **收集响应**：从 event.received 提取所有 (correlation_id, Response)
2. **注入 State**：按消息类型分别处理
   - 内置 msg_type（model_response / tool_result，§2.1 白名单）→ Engine 隐式处理，追加对应 ModelMessage
   - 自定义 msg_type → 由 `AgentConfig.processors` 注册的处理器处理（见下方）
3. **启动新一轮 think**：构造 ModelCall(Query) → 走 Checkpoint::BeforeModelCall → 发到 Bus
4. **进入新的等待循环**：可能是单 send 触发的新 event，也可能是 Checkpoint 注入的 multi-member event

**自定义 response processor**（通过 `AgentConfig.processors` 注册）：

```rust
/// App 实现的自定义消息类型 response 处理器。
/// 当 WaitEvent 收集到 msg_type 为 custom_type 的 Done 响应时，
/// Engine 查 AgentConfig.processors 表找到对应 processor，调用 process 注入 State。
pub trait ResponseProcessor: Send + Sync {
    fn process(&self, response: &serde_json::Value, state: &mut State);
}

// 示例：AgentConfig 声明 human_handoff 的 processor
let config = AgentConfig {
    routes: ...,
    checkpoint_rules: ...,
    processors: HashMap::from([
        (
            "human_handoff_result".into(),
            Arc::new(HumanHandoffProcessor::new()) as Arc<dyn ResponseProcessor>,
        ),
    ]),
    ..Default::default()
};
```

### 5.5 持久化

Event 列表随 State 一起序列化。Engine resume 时重放所有非 Cancelled event，重新计算每个 member 的 expected_receivers（基于当前 BusGraph）。

### 5.6 App-level Recovery 模型（多 Node 一致性）

> 与 §1.5 Multi-Bus 扩展配套。WaitEvent 持久化是 Engine 内部机制；本节定义**跨 Node 全局一致性**的 App-level 协议。

#### 5.6.1 两层恢复模型

| 层 | 机制 | 触发者 | 谁决定语义 |
|----|------|--------|----------|
| **节点级** | `Node::snapshot/restore`（§1.5.2） | 收到 barrier msg 或 App 显式调用 | Node 自己（Engine 存 State、模型节点存连接池） |
| **App 级** | `Bus::barrier(participants)` + 持久化存储 | App 在 Checkpoint 处显式调用 | App 自己（何时调用、存哪里、缺失如何处理） |

框架不强制 checkpoint 频率——App 决定每个 CheckpointRule 触发 App-level checkpoint 的条件。

#### 5.6.2 App-level Checkpoint 流程

```
Engine 到达 Checkpoint::RoundEnd
       │
       ▼
App 注册的 CheckpointRule::build 构造 AppCheckpoint intent（Command，Engine 不等）
       │
       ▼
AppCheckpoint Node 收到 msg → 触发 App-level checkpoint 逻辑
       │
       ├─ 1. 调用 bus.barrier(participants=[所有 stateful Node 的 id])
       │     │
       │     ▼
       │   Bus 广播 barrier msg，所有参与者 Node 收到
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
       │       （文件系统 / S3 / 数据库，由 App 决定）
       │
       └─ 4. missing Node → App 决定：retry / fail / 容忍
              │
              ▼
         持久化层 ack 后，App 发送 AppCheckpointDone(Command)
         Engine 继续下一轮 think
```

#### 5.6.3 典型 CheckpointRule 配置

```rust
CheckpointRule::new(
    name="app_checkpoint",
    trigger=Checkpoint::RoundEnd,
    when=|s| s.over_view.round_count % 5 == 0,  // 每 5 轮一次
    build=|s| Box::new(AppCheckpoint::new(stateful_node_ids=app.stateful_nodes())),
    route=Route::Strict(vec![NodeId::new("app_checkpoint_coordinator")]),
)
```

`app_checkpoint_coordinator` 是 App 提供的 Node，负责上述 4 步流程。它订阅 msg_types 包含 `app_checkpoint`。

#### 5.6.4 恢复流程

1. App 启动 → 创建 Bus 拓扑（top + 各 sub-Bus）+ Node 实例
2. 从持久化存储加载最近一次成功 checkpoint 的所有快照
3. 对每个 Node 调用 `restore(snapshot)`
4. Node `attach_to` 对应的 Bus
5. App 调用 `engine.restore(snap)` 拿到 state → `engine.run(state, ...)` 重发未完成 WaitEvent（§5.5）

恢复后，App 可选地：调用 `Bus::barrier` 验证所有 Node 都健康 ack，再宣布 Engine 可用。

#### 5.6.5 边界约定

- **AppCheckpoint 是 App Node，不是 Engine 内置机制**——保持 §10 第一条边界
- **snapshot 语义由 Node 决定**——框架不强加 schema，App 通过 NodeId 索引
- **barrier 超时策略由 App 决定**——框架只提供 timeout 参数
- **snapshot 并发由 Node 实现负责**——Node 内部用 RwLock/Mutex 保护状态；Node 内部负责超时控制（详见 §1.5.2）
- **Engine 不感知 recovery 发生**——Engine 只看到 Resume 信号，与正常 Chat() 不可区分

### 5.7 Node 掉线 + 超时处理（2026-06-30 新增）

> Engine 在 pending WaitEvent 期间如何处理"响应永远不会到"——两种触发源：`node_offline` lifecycle signal（节点进程死了）或超时（hang 住但未下线）。两种都路由到同一个 `OnMemberFailedHandler`，由 App 决策后续行为。
>
> **术语约定**：`WaitEvent` 是 Engine 内部的 pending message group（§5）；`node_online` / `node_offline` 是 Bus 发出的 lifecycle signal（msg_type 形式，附录节点上下线）。文档后续 "lifecycle signal" 一律指 Bus 侧的 `node_*` 信号，与 `WaitEvent` 区分。

#### 5.7.1 核心场景

```
Engine 发送 ModelCall(Query) → Strict(["primary_model"])
Engine park → 等响应
[primary_model crash] → Bus 发 node_offline
[primary_model hang] → Engine 内部 timer 超时（timeout）
Engine 仍 park，响应永远不会到
→ 历史上 Engine 会永远卡住
```

#### 5.7.2 决议：Fail + App hook

**Engine 行为**：
1. Engine 订阅 `node_offline` lifecycle signal（与 ModelCall/ToolExec 等并列）
2. 对 pending WaitEvent 的每个 member 跟踪其目标 NodeId + 投递时间
3. Engine 内部为每个 pending member 设置 timeout（默认从 AgentConfig 读取，可被 `route` 上的 msg_type-specific 配置覆盖）
4. 收到 `node_offline(node_id=X)` 或某 member 超时时，检查所有 pending event 的 members：
   - 若 X / 超时 member 是该 event 的某个 member 之一 → 该 member 标记为 "failed response"
   - 失败原因（`FailedReason::Offline` / `FailedReason::Timeout`）附在 member 上
5. 按 `WaitStrategy` 决定 event 后续：
   - `All`：任一 member failed → **event failed**（即使其他 member 还没响应）
   - `Any`：failed member 忽略，继续等待其他 member
   - `Count(n)`：failed 不计入成功数，App 决定 n 是按 "响应数" 还是 "完成数（响应+失败）"
6. event failed → Engine 触发 `OnMemberFailedHandler`（§5.7.3）
7. 默认行为（无 handler）：整个 session fail，Engine 进入 stopped 状态

#### 5.7.3 App hook 接口

```rust
pub enum FailedReason {
    /// 节点进程崩溃（Bus 发 node_offline）
    Offline,
    /// 节点 hang 住但未下线（Engine 内部 timer 触发）
    Timeout,
}

pub trait OnMemberFailedHandler: Send + Sync {
    /// Engine 在某 WaitEvent 的 member 因 node_offline 或超时而失败时调用
    /// 返回决定：
    ///   * FailSession — Engine 进入 stopped，返回 session 错误
    ///   * Retry(msg) — Engine 重发原 message 给原目标（App 显式决策）
    ///   * SwitchTo(new_route) — Engine 用新 route 重新解析 receiver
    ///   * IgnoreAndContinue — Engine 继续 wait（即便 strategy 是 All）
    fn on_member_failed(
        &self,
        event: EventId,
        member: CorrelationId,
        failed_node: NodeId,
        reason: FailedReason,
    ) -> MemberFailedAction;
}
```

**默认实现**（无 App 注册时）：`FailSession`

**App 注册示例**（通过 `AgentConfig.on_member_failed` 字段）：
```rust
let config = AgentConfig {
    routes: ...,
    checkpoint_rules: ...,
    on_member_failed: Some(Arc::new(MyFailureHandler::new())),
    ..Default::default()
};

let engine = EngineBuilder::new(bus=bus)
    .build(config=config)
    .await?;
```

#### 5.7.4 与 Retry 的关系（重试边界）

**Engine 只确保"信息发出 + 信息接收"，不解析 Receiver 内部错误，不自动重试**（2026-06-30 决议）：

| 谁负责 | 什么 | 例子 |
|--------|------|------|
| **执行节点自己** | 内部重试：API 限流（429）、网络瞬时失败 | ModelAdapter 看到 429 后 sleep 重试；McpNode 在 transient 错误时重试 |
| **Engine** | 只发 / 只收；不重试、不解析 Value 内部错误 | 业务错误（如模型返回 "permission denied"）作为 Value.content 正常返回；Engine 不区分 |
| **App（通过 handler）** | 显示决策：收到 FailedReason 后怎么办 | 注册 `OnMemberFailedHandler` 返回 Retry/SwitchTo/FailSession/IgnoreAndContinue |

**边界澄清**：
- Engine 内**无**自动重试机制——retry 永远是 App 显式通过 handler 决策
- Handler 返回 `Retry(msg)` 是 App 级别的重发决策（Engine 仅作为执行者），**不**等同于 Engine 内置重试
- 执行节点内部重试是 App 实现的细节（如 ModelAdapter 自己 sleep + retry），**不**是 Engine 行为
- Receiver 业务错误不产生 FailedResponse；所有响应都是 `Done(Value)`，Value 内容由 App processor 解释

**App handler 示例**（App 级别重试）：
```rust
impl OnMemberFailedHandler for MyFailureHandler {
    fn on_member_failed(&self, ..., reason: FailedReason) -> MemberFailedAction {
        match reason {
            FailedReason::Timeout if self.attempts < 3 => {
                self.attempts += 1;
                MemberFailedAction::Retry(self.last_msg.clone())
            }
            _ => MemberFailedAction::FailSession,
        }
    }
}
```

#### 5.7.5 不做的事

- ❌ Engine 不自动 park 等 Node 恢复——Engine 没法"等"一个可能永不上线的 Node
- ❌ Engine 不内置重试计数器——App 想要就用 handler
- ❌ Engine 不监听 `node_online` 自动重发——App 想要就在 handler 里实现
- ❌ Engine 不解析 Response::Done 内部字段——Value 语义由 App processor 解释
- ❌ Engine 不区分 "临时掉线" vs "永久掉线"——Bus 只发 node_offline，App 自己判断
- ❌ Engine 不执行节点内部重试——那是执行节点（如 ModelAdapter）自己的实现

## 6. State 变更

Engine 的 State 私有，外部节点不直接写入。

1. Engine 发消息时带上 state 片段（如 messages 副本）；同一 event 内的多条消息共享同一 state snapshot
2. 外部节点处理后返回结果
3. 如需修改 state，结果中包含 `replacement_messages` 字段
4. Engine：备份旧 messages → 替换 → 继续

Engine 不解析 memory/compact 的语义，只执行备份和替换。

## 7. AgentConfig

```rust
pub struct AgentConfig {
    pub agent_id: String,
    pub model_config: ModelConfig,
    /// 包含 `{{skills}}` 占位符的 system prompt 模板（**不含** `{{tools}}`，tools 通过 ModelCall.tools 字段传）。
    pub system_prompt_template: String,
    /// build() 后作为 system 消息追加到 messages 前部（保持最长前缀稳定，cache 命中率高）。
    /// 提取自 App 持久化的"用户身份/任务画像/历史摘要"等。运行时 extraction 的结果**不**实时追加到此处。
    pub initial_memory: Vec<String>,
    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,
    pub session_mode: SessionMode,
    pub permissions: PermissionConfig,
    pub allow_paths: Vec<String>,
    pub routes: HashMap<String, Route>,                   // msg_type → Route
    pub checkpoint_rules: Vec<CheckpointRule>,           // App 注入的触发器
    /// 自定义 msg_type 的 response 处理器（§5.4）。
    /// 内置 msg_type（model_call / tool_exec，§2.1 白名单）无需注册。
    pub processors: HashMap<String, Arc<dyn ResponseProcessor>>,
    /// Node 掉线时 Engine 等待响应失败的处理 hook（§5.7）。
    /// None 时使用默认行为（FailSession）。
    pub on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>,
    /// Tools 白名单 glob（§2.5.1）：include 为 None 或包含 `*` 表示收全部；空列表 = 不收
    pub tools_include: Option<Vec<String>>,
    pub tools_exclude: Vec<String>,
    pub skills_include: Option<Vec<String>>,
    pub skills_exclude: Vec<String>,
}
```

**所有 App 级配置统一进 AgentConfig**（2026-06-30 决议）：
- `EngineBuilder` 只保留 `new(bus)` 和 `build(config).await?` 两个方法
- 不再有 `register_processor` / `on_member_failed` 等链式调用
- App 改 config → 重新 `build` 一个新 Engine；不需要"在已 build 的 Engine 上追加 handler"

注意（2026-06-30）：移除原 `node_bindings: Vec<NodeBinding>`。具体 Node 通过 `bus.connect()` 上线，`EngineBuilder.build()` 时校验 routes 与当前 BusGraph 一致。

`AgentConfig` 不直接持有 ActionMessage 类型，而是通过 `routes` + `checkpoint_rules` 间接表达。Engine 不需要知道具体消息类型，只按字符串 msg_type 路由。

## 8. Bus 节点

Engine 不直接调用任何节点，只发消息。节点按 msg_type 订阅。

### 8.1 拦截型（Engine 无感）

| 节点 | 订阅 | 行为 |
|------|------|------|
| AuthNode | `tool_exec` | 读 AgentConfig → 放行/询问/拒绝 |
| SandboxNode | `tool_exec` | 读 allow_paths → 路径检查 |

### 8.2 处理型（Engine 等待响应）

| 节点 | 订阅 | 行为 |
|------|------|------|
| ModelAdapter | `model_call` | LLM API 调用（Engine 内置流程，无 App 自定义） |
| McpNode | `tool_exec` | 工具执行（经 Auth/Sandbox 拦截） |
| MemoryNode | `memory_op` | 抽取（Command only；运行时 retrieval 由模型 tool call 处理，详见 §11.1） |
| CompactionNode | `compact_op` | 上下文压缩 |
| SubagentNode（App 实现） | `subagent_op` | 委派子 Agent——**Engine 不内置**，App 自己实现的 Node（详见 §13.3） |
| HumanProxyNode（App 实现） | `human_handoff` | 人介入——**Engine 不内置**，App 自己实现的 Node |

### 8.3 纯观测（Engine 无感，不响应）

| 节点 | 订阅 | 行为 |
|------|------|------|
| TraceWriter | all | 落盘 JSONL |
| Logger | all | 日志输出 |

## 9. Engine 拥有 vs 不拥有

| Engine 拥有 | 不拥有（App / Node 提供） |
|------------|--------------------------|
| ReAct 状态机（idle/processing/waiting/stopped，§4.1） | State 所有权（State 由 App 持有，Engine 通过 &mut 借走） |
| ReAct 循环流程 | 具体 Node 实现（ModelAdapter 等） |
| 5 个 Checkpoint 位置 | Route 表 |
| 终止条件判断 | CheckpointRule 列表 |
| 在 &mut State 期间维护 messages / over_view / wait_events | NodeId / Capability 声明 |
| ActionMessage 子类型 | 持久化时机与存储后端 |
| System prompt 组装 | BusGraph 查询 |
| Turn/Round 计数 | |

## 10. 三条不可违反的边界

1. **Engine 不知道任何具体节点类型** —— Engine 代码不 `use` 任何具体 Node 实现所在的 crate；`ModelAdapter` / `McpNode` / `MemoryNode` 等节点类型名不出现在 Engine 代码。msg_type 字符串（`"model_call"` / `"tool_exec"` 等）是路由 key，与节点类型名解耦——**不构成边界违反**。
2. **Node 不知道 Engine 的存在** —— Node 只订阅 msg_types，不假设发送者是 Engine
3. **Checkpoint 是位置，不是消息类型** —— 5 个位置固定；具体发什么 msg 由 CheckpointRule 决定

## 11. 关键场景

### 11.1 MemoryOp：仅 Extract（Command）

```rust
/// 记忆抽取消息（2026-06-30 决议：移除 Retrieve）。
/// 运行时 memory 检索由模型主动发起 tool call（如 `memory_search`），
/// 走标准 tool_exec 流程；**不**是单独的 MemoryOp::Retrieve msg。
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

**双路 memory 机制**：
- **固定 memory（build 时）**：App 提供的 `config.initial_memory` 一次性追加到 messages 前缀
- **运行时 retrieval（tool call）**：模型调 `memory_search` 等 tool，走标准 `tool_exec` 流程
- **运行时 extraction（CheckpointRule）**：本节 MemoryOp::extract 是唯一触发点，Command intent（fire-and-forget），完成后**不**修改 messages——抽取结果在下次 session 加载时作为 `initial_memory` 出现

### 11.2 Multi-model 投票：Strict + Query

```rust
routes.insert("model_call".into(), Route::Strict(vec![
    NodeId::new(id="claude"),
    NodeId::new(id="gpt"),
    NodeId::new(id="deepseek"),
]));

// ModelCall 默认 intent=Query
// Engine park 等三个 model 都给最终响应
```

### 11.3 上下文压缩：Discovery + Query

```rust
// CompactOp 的 msg_type="compact_op"，Engine 查 AgentConfig.routes["compact_op"] 投递
CheckpointRule::new(
    "compact",
    Checkpoint::BeforeModelCall,
    |s| s.over_view.context_tokens as f64
        / s.over_view.model_context_window as f64 > 0.8,
    |s| Box::new(CompactOp::new(messages=s.messages.clone())),
)

// CompactOp 的 intent() = Query（§11.1 模式）→ Engine park 等所有 compactor 完成
// 压缩完成后才发 ModelCall，确保模型收到的是压缩后的上下文
```

### 11.4 自定义消息：human_handoff

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

## 12. 任务拆解（草案）

### 12.A Multi-Bus 基础设施（增量，§1.5 实现）

| # | 任务 | 内容 | 依赖 |
|---|------|------|------|
| 6.0.1 | Node trait 抽象 | `crates/arf-core/src/node.rs` 定义 `Node` trait（`id`/`snapshot`/`restore`/`on_message`） | — |
| 6.0.2 | NodeHandle 多 Bus 订阅 | 重构 `NodeHandle` 内部从单 mpsc 改成 `Vec<(BusId, mpsc::Receiver)>`；新增 `attach_to(bus)` / `send_via(bus, to, payload)` | 6.0.1 |
| 6.0.3 | BusId + Bus 标识 | `Bus` 加 `id: BusId`（UUID 或自增）；`Message` 加 `from_bus: Option<BusId>` 字段（兼容旧数据） | 6.0.2 |
| 6.0.4 | Bus::barrier() 原语 | `barrier(participants, timeout) -> BarrierReceipt`；broadcast barrier msg + oneshot 收集 ack | 6.0.3 |
| 6.0.5 | 测试 + 文档 | 现有 Bus 测试 0 修改通过；新增多 Bus / Barrier / snapshot 场景测试；本设计文档落地 | 6.0.4 |

### 12.B Engine 核心实现（原有任务，重排依赖）

| # | 任务 | 内容 | 依赖 |
|---|------|------|------|
| 6.1 | 核心类型定义 | `ActionMessage` trait、`MessageIntent`、`Route`、`Capability`、`State`、`OverView`、`Checkpoint`、`CheckpointRule` — 在 `crates/arf-core/src/` | 6.0.5 |
| 6.2 | Response 协议 | `Response::Done(Value)` 单形态；引擎 park 逻辑（Query 等全部、Command 不入队） | 6.1 |
| 6.3 | Engine 骨架 | `Engine` struct 实现 `Node` trait、AgentConfig、State 所有权、4 状态机、bus.connect | 6.0.5 + 6.1 |
| 6.4 | ReAct 主循环 | 5 个 Checkpoint 位置、fixed ModelCall↔ToolExec 循环、终止判断 | 6.3 |
| 6.5 | Checkpoint 系统 | 规则注册、when/build 调用顺序、intent 决定的 park 行为 | 6.4 |
| 6.6 | 等待队列 + Park/Resume | WaitEvent + PendingMessageWait、correlation_id 匹配、expected_receivers 计算、event strategy 触发、持久化（与 §5.6 App-level Recovery 配合） | 6.5 |
| 6.7 | Route 解析 | BusGraph 查询、Strict/Discovery 转换、多 receiver park 协调 | 6.3 |
| 6.8 | EngineBuilder API | `crates/arf-agent/src/builder.rs`、标准 CheckpointRule 构造器、`OnMemberFailedHandler` 注册、build() fail-fast 校验 | 6.6 + 6.7 |
| 6.9 | 集成测试 | MiniEngine + fixtures + ModelAdapter + McpNode 全链路；包含多 Bus 拓扑 fixture | 6.8 |
| 6.10 | Python API | PyO3 绑定 Engine + AgentConfig + EngineBuilder + Bus::barrier + Node trait | 6.8 |

### 12.C 域控制器示例（教学任务，§1.5.3 落地）

| # | 任务 | 内容 | 依赖 |
|---|------|------|------|
| 6.11 | MCP facade 示例 | `examples/domain_controller/` 演示 McpFacade Node 实现：top Bus ↔ MCP sub-Bus 转发 | 6.9 |
| 6.12 | App-level Recovery 示例 | `examples/recovery/` 演示 AppCheckpoint Node + Bus::barrier + 文件持久化 | 6.9 + 6.11 |

## 13. 待澄清 / 留待实现阶段

（2026-06-30 全部 5 条原始待澄清项已通过逐条讨论解决——见 §13.1）

### 13.1 已澄清

#### 来自本轮 §13 讨论的 5 项决议

- ~~`Task` 的具体形态~~ → **删除**（§2.4）。State 简化为 `messages + over_view`；所有 in-flight 操作由 WaitEvent 覆盖（§5）
- ~~Node 订阅 msg_types 的注册机制（filter vs 内部自过滤）~~ → **接收端过滤**（§1.5.2）。`attach_to(bus, filter)` 每条 Bus 订阅独立配置 filter；Node on_message 只看过滤后消息，不再需要内部按 (from_bus, msg_type) 二次匹配
- ~~Engine 启动时如何校验 node_id / capability 真实存在~~ → **删 NodeBinding + build() fail-fast**（§3）。`AgentConfig` 只声明 routes；`build()` 时校验当前 BusGraph 满足所有 routes；失败返回 `BuildError { missing_nodes, missing_capabilities }`
- ~~节点掉线时的 Engine 行为~~ → **Fail + App hook**（§5.7）。Engine 监听 node_offline，对 pending WaitEvent 的 member 标记 failed；按 WaitStrategy 处理；触发 `OnMemberFailedHandler` hook；默认 FailSession
- ~~SessionState 持久化时机~~ → **App 全权决定**（§1.5.2 + §5.6）。Engine.snapshot/restore 是机制；触发时机由 App 通过 CheckpointRule + Barrier 决定

#### 此前已澄清的 6 项（2026-06-30 multi-bus 修订）

- ~~`OverView` 字段的精确计算~~ → 见 §2.4 字段计算策略表。`context_tokens` 来自 API 响应的 `usage.prompt_tokens`；`runtime` 是 active time（processing 状态累计）；`model_context_window` 启动时从 ModelAdapter capabilities 读取
- ~~Multi-Bus 是否需要全局路由~~ → **不需要**。每条 Bus 独立；跨 Bus 由 Node 自己订阅多条 Bus 实现（manual bridging）
- ~~Node 跨 Bus 身份~~ → **全局唯一**。同一 NodeId 在所有订阅的 Bus 上是同一 Node（同一状态、同一身份）
- ~~域控制器是框架概念还是 App 模式~~ → **App 模式**。facade Node 是普通 Node，App 自己实现转发逻辑
- ~~多 Node 一致性方案~~ → **独立快照 + 可选 Barrier**（§5.6）。`Node::snapshot/restore` 必备；`Bus::barrier` 可选；App 决定 checkpoint 策略
- ~~Bus API 变更范围~~ → **最小侵入**（§1.5.2）。`Bus` 结构体不变；`NodeHandle` 内部多 mpsc；现有测试 0 修改通过

### 13.2 仍待实现阶段澄清

- `Bus::barrier` 的具体 ack 协议细节（msg type、correlation_id 命名、oneshot vs broadcast）
- `OnMemberFailedHandler` 的 Retry/SwitchTo 行为对 Engine 状态的副作用（是否触发新 Checkpoint、是否写 State）
- AppCheckpoint Node 与 Engine 的 CheckpointRule 集成方式（rule.build 返回 AppCheckpoint msg 类型需要新增）

### 13.3 不属于本阶段任务（2026-06-30 决议）

以下功能**不**在 Phase 6 Engine 设计范围内，App 自行实现：

- **Subagent 编排**：Engine 不内置 subagent 机制。App 通过 CheckpointRule 注入 `subagent_op`（msg_type 字符串），App 自己实现的 SubagentNode 订阅该 msg_type 并驱动子 Agent。Engine 不感知子 Agent 的存在与状态。
- **Peer Agent 通信**：Engine 不内置 peer-to-peer 协议。App 通过 `peer_message` msg_type 在多个 Engine 间传递消息，App 自己实现 PeerNode 路由逻辑。
- **多 Agent 协调**：投票 / 辩论 / 角色切换等模式都是 App 层模式，Engine 只提供 msg_type 抽象。

**核心边界**：Engine 可以驱动任意类型 Agent 的执行（model_call / tool_exec / 任何 msg_type 路由），但不内置任何特定 Agent 拓扑——Engine 是 mechanism，App 决定 topology。

## 14. Python API 展望

```python
from arf import Engine, EngineBuilder, AgentConfig, Route, Capability

config = AgentConfig(
    agent_id="assistant",
    # tools 不在 prompt 里；{{skills}} 由 Engine build() 时自动填
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
        # 每 5 轮提取记忆（route 来自 routes["memory_op"]）
        CheckpointRule.every_n_rounds(
            trigger=Checkpoint.RoundEnd,
            every_n=5,
            build=lambda s: MemoryOp.extract(messages=s.messages),
        ),
        # 上下文超 80% 触发压缩（route 来自 routes["compact_op"]）
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
    # 资源过滤（§2.5.1）
    tools_include=["local_mcp.read_file", "local_mcp.bash", "advanced_mcp.*"],
    tools_exclude=["advanced_mcp.dangerous_exec"],
    skills_include=["skill_hub.greet"],
    skills_exclude=[],
)

engine = await EngineBuilder.new(buses=[bus_top, bus_sub]).build(config=config)

# 框架不提供 Session；App 自己组装（2026-06-30 决议）
state = State.default()
cancel = CancellationToken()
output = await engine.run(state=state, user_input="Read /etc/hostname", cancel=cancel)
snap = engine.snapshot(state=state)  # 同步快照
```

### 14.1 Integration Test: 平铺模式 App（设计验证）

> 以下为集成测试代码，假设 Engine / EngineBuilder / Checkpoint / CheckpointRule / Route / Capability / MemoryOp / CompactOp 已按 §14 规范实现。
>
> 目的：通过真实组装一个**平铺模式**（所有 Node 在同一 Top Bus 上，Engine 不感知任何具体 Node 类型）的多轮对话 App，检验 Phase 6 设计的抽象干净程度。

#### 14.1.1 辅助 Node：MemoryNode + CompactionNode（mock）

```python
"""nodes.py — MemoryNode 和 CompactionNode 的 Python mock 实现。

这两个 Node 连接 Bus，分别订阅 memory_op / compact_op，
收到消息后 mock 处理并返回结果。它们不知道 Engine 的存在——
只按 msg_type 订阅、按 from 字段回复。

验证点：
  - Node 独立性（§10 第二条边界）：Node 只订阅 msg_types，不假设发送者是 Engine
  - 平铺模式：所有 Node 同在一个 Bus 上，filter 确保无串扰
  - Query/Command intent 区分：Engine 等 Query 的响应，不等 Command
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
        if action == "retrieve":
            query = msg.payload.get("query", "")
            return {"memories": [], "query": query}
        return None


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
        # mock: 保留 system + 最后 10 条，其余替换为摘要
        keep = messages[-10:] if len(messages) > 10 else messages
        return {
            "compacted_messages": keep,
            "summary": f"Compacted {max(0, len(messages) - 10)} messages",
            "original_count": len(messages),
            "compacted_count": len(keep),
        }
```

#### 14.1.2 平铺模式 App 组装 + 多轮对话

```python
"""app.py — 平铺模式 App：组装 Bus + Engine + 所有 Node，跑多轮对话。

架构（7 个 Node 同在一个 Top Bus）：

    Top Bus
    ├── engine/main            Engine（ReAct 循环）
    ├── model/deepseek         ModelAdapterNode（DeepSeek provider）
    ├── mcp/local              McpNode（本地 tools/ + skills/ 扫描）
    ├── memory/l1              MemoryNode（订阅 memory_op，每 5 轮 extract）
    ├── compactor/default      CompactionNode（订阅 compact_op，context > 80% trigger）
    ├── trace/obs              全量观测（ToMatch.All，打印所有消息）
    └── guard/path             PathSandbox（可选，路径安全检查）

验证点：
  1. 组装接口 — EngineBuilder.new(bus).route(...).add_checkpoint(...).build(config)
  2. Route 语义 — Strict（model_call → 精确 NodeId）vs Discovery（tool_exec → kind=mcp）
  3. Checkpoint 抽象 — every_n_rounds() / when_context_over() 构造器
  4. State 生命周期 — App 持有 State，engine.run(&mut state, ...) 多轮不丢状态
  5. Node 独立性 — MemoryNode/CompactionNode 不知道 Engine 的存在
  6. 平铺模式 — 所有 Node 在同一 Bus 上无消息串扰

运行：
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
# 0. 准备本地 tools/ 目录（McpNode 需要）
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

    # model/deepseek — 处理型 Node，Engine 等其响应
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

    # mcp/local — 处理型 Node，订阅 tool_exec
    mcp_node = McpNode.local(namespace="local", root=tools_root)
    await mcp_node.connect(bus)
    print(f"[app] mcp/local connected ({mcp_node.node_id})")

    # memory/l1 — 订阅 memory_op（mock）
    memory_node = MemoryNode()
    await memory_node.connect(bus)
    print("[app] memory/l1 connected")

    # compactor/default — 订阅 compact_op（mock）
    compactor_node = CompactionNode()
    await compactor_node.connect(bus)
    print("[app] compactor/default connected")

    # trace/obs — 纯观测 Node（ToMatch.All，不阻塞）
    trace_handle = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    async def trace_loop():
        """后台任务：打印所有 Bus 消息。"""
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
        # 示例输出：
        # [trace] model_call            engine/main                  → ['model/deepseek']
        # [trace] model_response        model/deepseek               → ['engine/main']
        # [trace] tool_exec             engine/main                  → ['mcp/local']
        # [trace] memory_op             engine/main [trigger=ROUND_END rule=extract_memory]
        #                                 → ['memory/l1']
        # 一眼区分 ReAct 步骤（无 trigger 标签）vs 规则触发（带 trigger 标签）

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

    engine = await EngineBuilder.new(buses=[bus_top, bus_sub]).build(config=config)
    print("\n[app] Engine built — routes + checkpoint_rules validated")

    # ── 1.5 App 持有 State（2026-06-30 决议：框架不提供 Session 抽象）──
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

        # 检查 Checkpoint 触发（mock 环境会打印）
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

    # 验证 Checkpoint 触发：第 5 轮 RoundEnd 应触发 memory extract
    # （mock 环境下 checkpoint_rules 的 when 条件满足即触发）
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

#### 14.1.3 设计验证结论

| # | 验证点 | 设计位置 | 结论 |
|---|--------|---------|------|
| 1 | **组装接口** | §3 App 装配模型 | `EngineBuilder.new(buses=[...]).build(config=AgentConfig{...})` 声明式 API，所有 App 配置进 AgentConfig |
| 2 | **Route 语义** | §2.3 Route | `Strict(["model/deepseek"])` 精确路由 + `Discovery(Capability("kind","mcp"))` 能力发现，语义一目了然 |
| 3 | **Checkpoint 抽象** | §2.5 CheckpointRule | `every_n_rounds()` 和 `when_context_over()` 两个标准构造器覆盖了典型需求；`name/trigger/when/build` 四元组足够灵活 |
| 4 | **State 生命周期** | §4 ReAct 循环 | App 持有 `State`，通过 `engine.run(&mut state, ...)` 多轮不丢状态，`over_view` 字段自动维护 |
| 5 | **Node 独立性** | §10 第二条边界 | MemoryNode/CompactionNode 只订阅 msg_type，不假设发送者是 Engine。mock 的实现只依赖 Bus API，不 import Engine |
| 6 | **平铺模式** | §1.5.3 扁平拓扑 | 7 个 Node 在同一 Bus 上无消息串扰——filter 在接收端过滤，每个 Node 只看到自己订阅的类型 |
| 7 | **Query vs Command** | §2.1 MessageIntent | model_call / tool_exec / compact_op 是 Query（Engine park 等响应）；MemoryOp.extract 是 Command（Engine 不等，fire-and-forget）。压缩必须在模型调用前完成，所以 compact_op 是 Query |

**设计改进发现：**

- ~~`Route.strict(node_ids=...)` 参类型不一致~~ → 已统一。Rust 侧用 `Vec<NodeId>`（§3），Python 侧接受 `list[str]` 内部转换为 NodeId（§14, §14.1.2）。
- ~~`EngineBuilder.build(config)` 多态~~ → 已统一。§3 和 §14 均使用 `EngineBuilder::new(bus).build(config)`。
- `Capability` Python 构造简化为 `Capability(key="k", value="v")`（vs Rust 侧 `Capability::new(key, value)`）——语言惯例差异，合理。
- McpNode Python 绑定缺少 `shutdown()` 方法——app.py 第 1530 行用 `hasattr` 兜底，后续 Rust MCP 实现需补充。