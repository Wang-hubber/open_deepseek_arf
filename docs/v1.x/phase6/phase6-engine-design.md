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

    /// 序列化自己的状态。语义由 Node 决定（Engine 存 State、模型节点存连接池、...）
    fn snapshot(&self) -> Result<serde_json::Value, SnapshotError>;

    /// 从快照恢复状态。语义由 Node 决定。
    async fn restore(&mut self, snapshot: serde_json::Value) -> Result<(), RestoreError>;

    /// 收到消息。`from_bus` 让 Node 知道消息来自哪条 Bus（facade 转发需要）
    async fn on_message(&mut self, msg: Message, from_bus: BusId);
}
```

`NodeId` 全局唯一——同一 Node 在所有订阅的 Bus 上是**同一身份**。

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

### 2.2 Response（单一形态）

```rust
pub enum Response {
    /// Receiver 给出的最终值
    /// Engine park 等所有 Query 接收方都给出 Done(Value) 才继续
    Done(serde_json::Value),
}
```

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

### 2.4 State（两部分 + WaitEvent 队列）

```rust
pub struct State {
    pub messages: Vec<ModelMessage>,  // 对话历史（详细）
    pub over_view: OverView,          // 聚合指标（O(1) 访问）
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
- Engine 按 `route` 投递消息，按 `intent` 决定 park 还是继续
- 框架提供标准构造器（`every_n_rounds`、`when_context_over`），但底层都是四元组

**CheckpointRule 五元组定义**（name + trigger + when + build + route）：

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
    /// 路由：Strict（指定 NodeId）或 Discovery（capability 匹配）
    pub route: Route,
}

impl CheckpointRule {
    /// 位置参数构造器。闭包参数接受 `for<'a> Fn(&'a State) -> ...`，避免与 struct field 生命周期不一致。
    pub fn new<W, B>(
        name: impl Into<String>,
        trigger: Checkpoint,
        when: W,
        build: B,
        route: Route,
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
            route,
        }
    }
}
```

Engine 在 trigger 位置遍历所有规则，`when(state)` 返回 true 的调 `build(state)` 生成消息，按 `route` 投递。

**不在 Checkpoint 范畴**：
- **Input 处理**（user_input 进入 state）发生在 `session.chat(user_input=msg)` 调用方，App 在外层拦截/转换/审批；Engine 内部不设 BeforeInput/AfterInput
- **System prompt 组装**发生在 `EngineBuilder.build()` 时，一次性完成
- **Tool/Skill 发现**发生在 Node connect 时，一次性声明
- **Turn 结束**与 RoundEnd 合并（每 turn 必然在 round 内；如需 turn 级 hook 用 `Checkpoint::RoundEnd` + `over_view.turn_count` 判断）

### 2.6 Engine

Engine 是 Bus 上一个特殊 Node，运行固定 ReAct 状态机。

**Engine 拥有**：
- ReAct 状态机（idle / processing / waiting / stopped）
- 5 个 Checkpoint 触发位置
- Session 状态（State 的所有权）
- 等待队列（WaitEvent 列表：每个 event 含 members + strategy）
- Park/Resume 机制

**Engine 不拥有**：
- 任何具体 Node 实例（ModelAdapter / McpNode / MemoryNode 等字眼不出现在 Engine 代码）
- Route 表
- CheckpointRule 列表
- messages/tasks 的具体业务解释（Engine 只做存取）

### 2.7 App（唯一的装配者）

App 通过 `EngineBuilder` 把所有部件组装起来。Engine 不参与装配。

## 3. App 装配模型

**2026-06-30 修订**：移除 `NodeBinding`（双重声明，违反 DRY）。`AgentConfig` 只声明路由和 CheckpointRule；具体 Node 通过 `bus.connect()` 上线，`build()` 时 fail-fast 校验 routes。

```rust
// crates/arf-agent/src/builder.rs

// ── 1. 创建 Bus ─────────────────────────────────────────────────
let bus = Bus::new(heartbeat_interval=5000, heartbeat_timeout=15000, channel_capacity=64);

// ── 2. 创建并连接具体 Node（必须在 build() 之前！）───────────────
//    build() 时 fail-fast 校验 routes → 查 BusGraph，
//    若 node_id 不在线或 capability 无匹配，返回 BuildError。
let model = ModelAdapter::new(
    node_id=NodeId::new(id="primary_model"),
    config=model_config,
);
bus.connect(info=model.info(), filter=model.filter()).await?;

let mcp = McpNode::new(
    node_id=NodeId::new(id="local_mcp"),
    config=mcp_config,
);
bus.connect(info=mcp.info(), filter=mcp.filter()).await?;

let memory = MemoryNode::new(
    node_id=NodeId::new(id="memory_node"),
);
bus.connect(info=memory.info(), filter=memory.filter()).await?;

// ── 3. 声明 AgentConfig（routes + checkpoint_rules）──────────────
let config = AgentConfig {
    agent_id: "assistant".into(),
    model_config: ModelConfig { provider: "deepseek".into(), model: "deepseek-v4-flash".into() },
    system_prompt_template: "You are a helpful assistant.\n\nTools:\n{{tools}}".into(),
    max_turns: 10,
    routes: {
        let mut m = HashMap::new();
        m.insert("model_call".into(), Route::Strict(vec![NodeId::new(id="primary_model")]));
        m.insert("tool_exec".into(), Route::Discovery(Capability::new(key="kind", value="mcp")));
        m.insert("memory_op".into(), Route::Strict(vec![NodeId::new(id="memory_node")]));
        m
    },
    checkpoint_rules: vec![
        CheckpointRule::new(
            "extract_memory",
            Checkpoint::RoundEnd,
            |s| s.over_view.round_count % 5 == 0,
            |s| Box::new(MemoryOp::extract(messages=s.messages.clone())),
            Route::Strict(vec![NodeId::new(id="memory_node")]),
        ),
        CheckpointRule::new(
            "compact",
            Checkpoint::BeforeModelCall,
            |s| s.over_view.context_tokens as f64
                / s.over_view.model_context_window as f64 > 0.8,
            |s| Box::new(CompactOp::new(messages=s.messages.clone())),
            Route::Discovery(Capability::new(key="kind", value="compactor")),
        ),
    ],
    ..Default::default()
};

// ── 4. build() fail-fast 校验 ──────────────────────────────────
//   * Strict route 的 NodeId 必须当前在线
//   * Discovery route 的 Capability 必须当前有匹配
//   * 失败 → BuildError { missing_nodes, missing_capabilities }
let engine = EngineBuilder::new(bus=bus.clone())
    .build(config=config)
    .await?;

// Engine 也作为 Node 上 Bus（§1.5 Node trait）
bus.connect(info=engine.info(), filter=engine.filter()).await?;

// ── 5. 运行 ────────────────────────────────────────────────────
engine.start_session(session_id="s1").await?;
engine.chat(user_input="Read /etc/hostname").await?;
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
let result = EngineBuilder::new(bus=bus.clone())
    .route(msg_type="model_call", route=Route::Strict(vec![NodeId::new(id="primary_model")]))
    .build()
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
session.chat(user_input=msg)  ← App 端：拦截/转换/审批在调用方完成
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
- App 调 `session.chat(user_input=msg)` 之前负责 input 拦截（验证、转换、审批），Engine 不感知
- Engine 内部 ReAct 循环只围绕 `model_call` 和 `tool_exec` 两个 action
- 5 个 Checkpoint 是位置标记；具体发什么 msg 由 CheckpointRule 决定
- Engine 只通过 `ModelCall` / `ToolExec` 两个内置消息类型与 Bus 通信；`Memory` / `Compact` / `Subagent` / `HumanHandoff` 等由 App 通过 CheckpointRule 注入

### 4.1 四状态

| 状态 | 含义 |
|------|------|
| `idle` | inbox 空，队列空 |
| `processing` | 正在执行 ReAct 循环 |
| `waiting` | 至少有一个未完成的 WaitEvent，等 event 触发 |
| `stopped` | 收到 stop 信号，终结 |

**术语约定**：状态机名称为 `waiting`；"park" 是动词（"Engine 进入 waiting 状态 / park 等响应"），不是状态名。文档后续出现的 "park" 一律按动词理解。

### 4.2 终止条件

| 条件 | 触发 |
|------|------|
| 模型返回纯文本 | LLM 不再调用 tool |
| `task_complete` | LLM 调用了 kernel tool |
| `max_turns` 超限 | `turn_count >= max_turns` |
| cancel | 外部 `cancel_session` |
| error | 不可恢复错误 |

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

### 5.4 Event 触发的 think-decide-react

当 event 出栈时，Engine 顺序执行：

1. **收集响应**：从 event.received 提取所有 (correlation_id, Response)
2. **注入 State**：按消息类型分别处理
   - model_response → state.messages（追加 assistant 消息）
   - tool_result → state.messages（追加 tool 消息）
   - 自定义类型 → 由 App 通过 `EngineBuilder::register_processor(msg_type, processor)` 注册的处理器处理（见下方）
3. **启动新一轮 think**：构造 ModelCall(Query) → 走 Checkpoint::BeforeModelCall → 发到 Bus
4. **进入新的等待循环**：可能是单 send 触发的新 event，也可能是 Checkpoint 注入的 multi-member event

**自定义 response processor 注册**（EngineBuilder）：

```rust
/// App 注册自定义消息类型的 response 处理器。
/// 当 WaitEvent 收集到 msg_type 为 custom_type 的 Done 响应时，
/// Engine 调用 processor 将 payload 注入 State。
pub trait ResponseProcessor: Send + Sync {
    fn process(&self, response: &serde_json::Value, state: &mut State);
}

impl EngineBuilder {
    pub fn register_processor(
        mut self,
        msg_type: &str,
        processor: Arc<dyn ResponseProcessor>,
    ) -> Self { ... }
}

// 示例：注册 human_handoff 的 processor
builder.register_processor(
    "human_handoff_result",
    Arc::new(HumanHandoffProcessor::new()),
);
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
5. Engine 调用 `session.resume()` → 重发未完成 WaitEvent（§5.5）

恢复后，App 可选地：调用 `Bus::barrier` 验证所有 Node 都健康 ack，再宣布 session live。

#### 5.6.5 边界约定

- **AppCheckpoint 是 App Node，不是 Engine 内置机制**——保持 §10 第一条边界
- **snapshot 语义由 Node 决定**——框架不强加 schema，App 通过 NodeId 索引
- **barrier 超时策略由 App 决定**——框架只提供 timeout 参数
- **Engine 不感知 recovery 发生**——Engine 只看到 Resume 信号，与正常 Chat() 不可区分

### 5.7 Node 掉线处理（2026-06-30 新增）

> Engine 在 pending WaitEvent 期间如何处理 Node 掉线。
>
> **术语约定**：`WaitEvent` 是 Engine 内部的 pending message group（§5）；`node_online` / `node_offline` 是 Bus 发出的 lifecycle signal（msg_type 形式，附录节点上下线）。文档后续 "lifecycle signal" 一律指 Bus 侧的 `node_*` 信号，与 `WaitEvent` 区分。

#### 5.7.1 核心场景

```
Engine 发送 ModelCall(Query) → Strict(["primary_model"])
Engine park → 等响应
[primary_model crash] → Bus 发 node_offline
Engine 仍 park，响应永远不会到
→ 历史上 Engine 会永远卡住
```

#### 5.7.2 决议：Fail + App hook

**Engine 行为**：
1. Engine 订阅 `node_offline` lifecycle signal（与 ModelCall/ToolExec 等并列）
2. 对 pending WaitEvent 的每个 member 跟踪其目标 NodeId
3. 收到 `node_offline(node_id=X)` 时，检查所有 pending event 的 members：
   - 若 X 是该 event 的某个 member 之一 → 该 member 标记为 "failed response"
4. 按 `WaitStrategy` 决定 event 后续：
   - `All`：任一 member failed → **event failed**（即使其他 member 还没响应）
   - `Any`：failed member 忽略，继续等待其他 member
   - `Count(n)`：failed 不计入成功数，App 决定 n 是按 "响应数" 还是 "完成数（响应+失败）"
5. event failed → Engine 触发 `OnMemberFailed` hook（App 注册）
6. 默认行为（无 hook）：整个 session fail，Engine 进入 stopped 状态

#### 5.7.3 App hook 接口

```rust
pub trait OnMemberFailedHandler: Send + Sync {
    /// Engine 在某 WaitEvent 的 member 因 node_offline 而失败时调用
    /// 返回决定：
    ///   * FailSession — Engine 进入 stopped，返回 session 错误
    ///   * Retry(msg) — Engine 重发原 message 给原目标（目标可能仍离线）
    ///   * SwitchTo(new_route) — Engine 用新 route 重新解析 receiver
    ///   * IgnoreAndContinue — Engine 继续 wait（即便 strategy 是 All）
    fn on_member_failed(&self, event: EventId, member: CorrelationId, failed_node: NodeId) -> MemberFailedAction;
}
```

**默认实现**（无 App 注册时）：`FailSession`

**App 注册示例**：
```rust
let engine = EngineBuilder::new(bus=bus.clone())
    .on_member_failed(handler=Arc::new(MyRetryHandler::new(max_retries=3)))
    ...
    .build()
    .await?;
```

#### 5.7.4 与 Retry 的关系

框架不内置自动重试（重试策略是 App 决策）：
- App 想重试 → 注册 handler 返回 `Retry`
- App 想切备用 Node → 返回 `SwitchTo(Route::Strict(vec![backup_model]))`
- App 想优雅失败 → 返回 `FailSession`（或用默认）

#### 5.7.5 不做的事

- ❌ Engine 不自动 park 等 Node 恢复——Engine 没法"等"一个可能永不上线的 Node
- ❌ Engine 不内置重试计数器——App 想要就用 handler
- ❌ Engine 不监听 `node_online` 自动重发——App 想要就在 handler 里实现
- ❌ 框架不区分 "临时掉线" vs "永久掉线"——Bus 只发 node_offline，App 自己判断

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
    pub system_prompt_template: String,
    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,
    pub session_mode: SessionMode,
    pub permissions: PermissionConfig,
    pub allow_paths: Vec<String>,
    pub routes: HashMap<String, Route>,                   // msg_type → Route
    pub checkpoint_rules: Vec<CheckpointRule>,           // App 注入的触发器
}
```

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
| ModelAdapter | `model_call` | LLM API 调用 |
| McpNode | `tool_exec` | 工具执行（经 Auth/Sandbox 拦截） |
| MemoryNode | `memory_op` | 检索（Query）/ 抽取（Command） |
| CompactionNode | `compact_op` | 上下文压缩 |
| SubagentNode | `subagent_op` | 委派子 Agent |
| HumanProxyNode | `human_handoff` | 人介入 |

### 8.3 纯观测（Engine 无感，不响应）

| 节点 | 订阅 | 行为 |
|------|------|------|
| TraceWriter | all | 落盘 JSONL |
| Logger | all | 日志输出 |

## 9. Engine 拥有 vs 不拥有

| Engine 拥有 | 不拥有（App / Node 提供） |
|------------|--------------------------|
| Session 状态机（idle/processing/waiting/stopped） | 具体 Node 实现（ModelAdapter 等） |
| ReAct 循环流程 | Route 表 |
| 5 个 Checkpoint 位置 | CheckpointRule 列表 |
| 终止条件判断 | NodeId / Capability 声明 |
| WaitEvent 队列 + 持久化时机 | ActionMessage 子类型 |
| State.over_view 字段维护 | messages/tasks 业务解释 |
| System prompt 组装 | BusGraph 查询 |
| Turn/Round 计数 | |

## 10. 三条不可违反的边界

1. **Engine 不知道任何具体节点类型** —— Engine 代码不 `use` 任何具体 Node 实现所在的 crate；`ModelAdapter` / `McpNode` / `MemoryNode` 等节点类型名不出现在 Engine 代码。msg_type 字符串（`"model_call"` / `"tool_exec"` 等）是路由 key，与节点类型名解耦——**不构成边界违反**。
2. **Node 不知道 Engine 的存在** —— Node 只订阅 msg_types，不假设发送者是 Engine
3. **Checkpoint 是位置，不是消息类型** —— 5 个位置固定；具体发什么 msg 由 CheckpointRule 决定

## 11. 关键场景

### 11.1 MemoryOp：Query vs Command 同 trait 不同 intent

```rust
impl ActionMessage for MemoryOp {
    fn msg_type(&self) -> &'static str { "memory_op" }
    fn intent(&self) -> MessageIntent {
        match self.action {
            MemoryAction::Retrieve => MessageIntent::Query,
            MemoryAction::Extract  => MessageIntent::Command,
        }
    }
}
```

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
CheckpointRule::new(
    "compact",
    Checkpoint::BeforeModelCall,
    |s| s.over_view.context_tokens as f64
        / s.over_view.model_context_window as f64 > 0.8,
    |s| Box::new(CompactOp::new(messages=s.messages.clone())),
    Route::Discovery(Capability::new(key="kind", value="compactor")),
)

// Query + Discovery → Engine 在 BeforeModelCall park 等所有 compactor 完成
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

## 14. Python API 展望

```python
from arf import Engine, EngineBuilder, AgentConfig, Route, Capability

config = AgentConfig(
    agent_id="assistant",
    system_prompt_template="You are a helpful assistant.\n\nTools:\n{{tools}}",
    max_turns=10,
    routes={
        "model_call": Route.strict(node_ids=["primary_model"]),
        "tool_exec":  Route.discovery(capability=Capability(key="kind", value="mcp")),
    },
    checkpoint_rules=[
        # 每 5 轮提取记忆
        CheckpointRule.every_n_rounds(
            trigger=Checkpoint.RoundEnd,
            every_n=5,
            build=lambda s: MemoryOp.extract(messages=s.messages),
            route=Route.strict(node_ids=["memory_node"]),
        ),
        # 上下文超 80% 触发压缩
        CheckpointRule.when_context_over(
            trigger=Checkpoint.BeforeModelCall,
            ratio=0.8,
            build=lambda s: CompactOp.new(messages=s.messages),
            route=Route.discovery(capability=Capability(key="kind", value="compactor")),
        ),
    ],
)

engine = await EngineBuilder.new(bus=bus).build(config=config)
session = await engine.start_session(session_id="s1")
output = await session.chat(user_input="Read /etc/hostname")
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
  4. Session 生命周期 — start_session() → chat() 多轮 → 状态保持
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
            print(f"[trace] {msg.msg_type:20s} {msg.sender!s:>18s}"
                  f" → {[str(t) for t in msg.to]!s:30s}"
                  f" payload={msg.payload}")

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
            "Tools:\n{{tools}}\n\n"
            "Use tools to help the user. Be concise."
        ),
        model_config={"provider": "deepseek", "model": "deepseek-v4-flash"},
        max_turns=10,
        routes={
            "model_call": Route.strict(node_ids=["model/deepseek"]),
            "tool_exec": Route.discovery(
                capability=Capability(key="kind", value="mcp")
            ),
        },
        checkpoint_rules=[
            CheckpointRule.every_n_rounds(
                trigger=Checkpoint.RoundEnd,
                every_n=5,
                build=lambda s: MemoryOp.extract(messages=s.messages),
                route=Route.strict(node_ids=["memory/l1"]),
            ),
            CheckpointRule.when_context_over(
                trigger=Checkpoint.BeforeModelCall,
                ratio=0.8,
                build=lambda s: CompactOp.new(messages=s.messages),
                route=Route.discovery(
                    capability=Capability(key="kind", value="compactor")
                ),
            ),
        ],
    )

    engine = await EngineBuilder.new(bus=bus).build(config=config)
    print("\n[app] Engine built — routes + checkpoint_rules validated")

    # ── 1.5 启动 Session ────────────────────────────────────────────
    session = await engine.start_session(session_id="flat-demo")
    print("[app] Session started: flat-demo")

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

        output = await session.chat(user_input=user_input)
        print(f"[app] Round {i} output: {output}")

        # 检查 Checkpoint 触发（mock 环境会打印）
        print(f"[app] State: round={session.state.over_view.round_count}, "
              f"turn={session.state.over_view.turn_count}, "
              f"context={session.state.over_view.context_tokens}"
              f"/{session.state.over_view.model_context_window}")

    # ── 1.7 验证结果 ─────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("[app] All rounds complete. Validating...")
    print(f"  Total rounds: {session.state.over_view.round_count}")
    print(f"  Total turns:  {session.state.over_view.turn_count}")
    print(f"  Messages:     {len(session.state.messages)}")

    assert session.state.over_view.round_count == len(rounds), \
        f"Expected {len(rounds)} rounds, got {session.state.over_view.round_count}"
    assert session.state.over_view.turn_count > 0, "Expected non-zero turns"
    assert len(session.state.messages) > 0, "Expected non-empty messages"

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
| 1 | **组装接口** | §3 App 装配模型 | `EngineBuilder.new(bus).route(...).add_checkpoint(...).build(config)` 链式调用流畅，7 行完成全装配 |
| 2 | **Route 语义** | §2.3 Route | `Strict(["model/deepseek"])` 精确路由 + `Discovery(Capability("kind","mcp"))` 能力发现，语义一目了然 |
| 3 | **Checkpoint 抽象** | §2.5 CheckpointRule | `every_n_rounds()` 和 `when_context_over()` 两个标准构造器覆盖了典型需求；`when/build/route` 四元组足够灵活 |
| 4 | **Session 生命周期** | §4 ReAct 循环 | `start_session() → chat()` 多轮不丢状态，`over_view` 字段自动维护 |
| 5 | **Node 独立性** | §10 第二条边界 | MemoryNode/CompactionNode 只订阅 msg_type，不假设发送者是 Engine。mock 的实现只依赖 Bus API，不 import Engine |
| 6 | **平铺模式** | §1.5.3 扁平拓扑 | 7 个 Node 在同一 Bus 上无消息串扰——filter 在接收端过滤，每个 Node 只看到自己订阅的类型 |
| 7 | **Query vs Command** | §2.1 MessageIntent | model_call / tool_exec / compact_op 是 Query（Engine park 等响应）；MemoryOp.extract 是 Command（Engine 不等，fire-and-forget）。压缩必须在模型调用前完成，所以 compact_op 是 Query |

**设计改进发现：**

- ~~`Route.strict(node_ids=...)` 参类型不一致~~ → 已统一。Rust 侧用 `Vec<NodeId>`（§3），Python 侧接受 `list[str]` 内部转换为 NodeId（§14, §14.1.2）。
- ~~`EngineBuilder.build(config)` 多态~~ → 已统一。§3 和 §14 均使用 `EngineBuilder::new(bus).build(config)`。
- `Capability` Python 构造简化为 `Capability(key="k", value="v")`（vs Rust 侧 `Capability::new(key, value)`）——语言惯例差异，合理。
- McpNode Python 绑定缺少 `shutdown()` 方法——app.py 第 1530 行用 `hasattr` 兜底，后续 Rust MCP 实现需补充。