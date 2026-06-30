# Phase 6 — Engine 设计

> 依赖：Phase 1 (Bus), Phase 4 (ModelAdapter), Phase 5 (MCP), arf-state
> 状态：设计（重构中）
> 取代：原 6 消息 enum / ack 双控制 / YAML 检查点方案

## 1. 核心思想

Engine 做减法。它是 Bus 上的一个 Actor：维护 AgentConfig + State，在 ReAct 循环中按订阅式触发器收发消息。

**所有外部交互走 Bus，没有例外。** Engine 不直接调 Provider、不直接调 HookRunner、不直接调 MCP。它甚至**不知道任何具体节点类型的存在**。

```
┌──────────────────────────────────────┐
│  Engine (Actor)                      │
│                                      │
│  AgentConfig  +  State               │
│    ├── messages  (Vec<Message>)      │
│    ├── tasks     (Vec<Task>)         │
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
- Capabilities 在 connect 时声明，**不变直到 disconnect + reconnect**；运行时变更需重连
- Engine 缓存解析结果，收到 `node_online` / `node_offline` 事件时失效缓存

**边界场景**：
- 无匹配 → Engine 抛 `NoReceiver` 错误，App 决定 fail-fast / 降级 / 重试
- 单匹配 → 退化为 Strict 单 receiver 行为
- 多匹配 → 按 Discovery 语义（Query 等全部，Command 全发）

### 2.4 State（三部分）

```rust
pub struct State {
    pub messages: Vec<Message>,      // 对话历史（详细）
    pub tasks: Vec<Task>,            // 进行中 / 挂起任务
    pub over_view: OverView,         // 聚合指标（O(1) 访问）
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

`over_view` 字段由 Engine 在每个 Checkpoint 转移点维护。`messages` / `tasks` 是底层详细数据，Checkpoint 条件复杂时可自行遍历。

**字段计算策略**：

| 字段 | 计算方式 | 时机 |
|------|---------|------|
| `round_count` | `state.messages` 中 `role=user` 的条数（或 Engine 内部计数器） | 每次 chat() +1 |
| `turn_count` | Engine 内部计数器；每发一次 model_call/tool_exec +1 | ReAct 转移点 |
| `context_tokens` | **API usage 捕获**：从 model_call 响应的 `usage.prompt_tokens` 取 | 每次 model_call 响应后写入 |
| `model_context_window` | 启动时从 ModelAdapter 的 capabilities 读取；AgentConfig 可覆盖 | EngineBuilder.build() 时 |
| `runtime` | Active time（仅 Engine 处于 `processing` 状态的累计时长，不含 `waiting` / `parked`） | 状态机转移点累加 |
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

```rust
// crates/arf-agent/src/builder.rs

let engine = EngineBuilder::new(bus=bus.clone())
    // ── 注册节点 ──
    .register_node(binding=NodeBinding::new(
        node_id=NodeId::new(id="primary_model"),
        subscriptions=&["model_call"],
    ))
    .register_node(binding=NodeBinding::new(
        node_id=NodeId::new(id="local_mcp"),
        subscriptions=&["tool_exec"],
    ))
    .register_node(binding=NodeBinding::new(
        node_id=NodeId::new(id="memory_node"),
        subscriptions=&["memory_op"],
    ))

    // ── 路由表（msg_type → Route）──
    .route(msg_type="model_call", route=Route::Strict(vec![NodeId::new(id="primary_model")]))
    .route(msg_type="tool_exec", route=Route::Discovery(Capability::new(key="kind", value="mcp")))
    .route(msg_type="memory_op", route=Route::Strict(vec![NodeId::new(id="memory_node")]))

    // ── Checkpoint 触发器 ──
    .add_checkpoint(rule=CheckpointRule::new(
        name="extract_memory",
        trigger=Checkpoint::RoundEnd,
        when=|s| s.over_view.round_count > 1,
        build=|s| Box::new(MemoryOp::extract(messages=s.messages.clone())),
        route=Route::Strict(vec![NodeId::new(id="memory_node")]),
    ))
    .add_checkpoint(rule=CheckpointRule::new(
        name="compact",
        trigger=Checkpoint::BeforeModelCall,
        when=|s| s.over_view.context_tokens as f64
            / s.over_view.model_context_window as f64 > 0.8,
        build=|s| Box::new(CompactOp::new(messages=s.messages.clone())),
        route=Route::Discovery(Capability::new(key="kind", value="compactor")),
    ))

    .build()
    .await?;

// ── 创建并连接具体节点 ──
let model = ModelAdapter::new(
    node_id=NodeId::new(id="primary_model"),
    config=model_config,
);
model.subscribe_to(msg_types=&["model_call"]);
bus.connect(info=model.info(), filter=model.filter()).await?;

let mcp = McpNode::new(
    node_id=NodeId::new(id="local_mcp"),
    config=mcp_config,
);
mcp.subscribe_to(msg_types=&["tool_exec"]);
bus.connect(info=mcp.info(), filter=mcp.filter()).await?;

let engine_handle = bus.connect(info=engine.info(), filter=engine.filter()).await?;
engine.start_session(session_id="s1").await?;
engine.chat(user_input="Read /etc/hostname").await?;
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
   - 自定义类型 → 由 App 注册的 processor 处理
3. **启动新一轮 think**：构造 ModelCall(Query) → 走 Checkpoint::BeforeModelCall → 发到 Bus
4. **进入新的等待循环**：可能是单 send 触发的新 event，也可能是 Checkpoint 注入的 multi-member event

### 5.5 持久化

Event 列表随 State 一起序列化。Engine resume 时重放所有非 Cancelled event，重新计算每个 member 的 expected_receivers（基于当前 BusGraph）。

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
    pub model: ModelConfig,
    pub system_prompt_template: String,
    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,
    pub session_mode: SessionMode,
    pub permissions: PermissionConfig,
    pub allow_paths: Vec<String>,
    pub routes: HashMap<String, Route>,                   // msg_type → Route
    pub checkpoint_rules: Vec<CheckpointRule>,           // App 注入的触发器
    pub node_bindings: Vec<NodeBinding>,                  // 节点注册表
}
```

注意：`AgentConfig` 不再直接持有 ActionMessage 类型，而是通过 `routes` + `checkpoint_rules` 间接表达。Engine 不需要知道具体消息类型，只按字符串 msg_type 路由。

## 8. Bus 节点

Engine 不直接调用任何节点，只发消息。节点按 msg_type 订阅。

### 8.1 拦截型（Engine 无感）

| 节点 | 订阅 | 行为 |
|------|------|------|
| AuthNode | `ToolExec` | 读 AgentConfig → 放行/询问/拒绝 |
| SandboxNode | `ToolExec` | 读 allow_paths → 路径检查 |

### 8.2 处理型（Engine 等待响应）

| 节点 | 订阅 | 行为 |
|------|------|------|
| ModelAdapter | `ModelCall` | LLM API 调用 |
| McpNode | `ToolExec` | 工具执行（经 Auth/Sandbox 拦截） |
| MemoryNode | `MemoryOp` | 检索（Query）/ 抽取（Command） |
| CompactionNode | `CompactOp` | 上下文压缩 |
| SubagentNode | `SubagentOp` | 委派子 Agent |
| HumanProxyNode | `HumanHandoff` | 人介入 |

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

1. **Engine 不知道任何具体节点类型** —— `ModelAdapter` / `McpNode` / `MemoryNode` 等字眼不出现在 Engine 代码
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
.route(msg_type="model_call", route=Route::Strict(vec![
    NodeId::new(id="claude"),
    NodeId::new(id="gpt"),
    NodeId::new(id="deepseek"),
]))

// ModelCall 默认 intent=Query
// Engine park 等三个 model 都给最终响应
```

### 11.3 上下文压缩：Discovery + Query

```rust
CheckpointRule::new(
    name="compact",
    trigger=Checkpoint::BeforeModelCall,
    when=|s| s.over_view.context_tokens > (s.over_view.model_context_window * 4 / 5),
    build=|s| Box::new(CompactOp::new(messages=s.messages.clone())),
    route=Route::Discovery(Capability::new(key="kind", value="compactor")),
)

// 多 compactor 节点协同压缩
// Query + Discovery → Engine 等所有 compactor 完成
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

| # | 任务 | 内容 |
|---|------|------|
| 6.1 | 核心类型定义 | `ActionMessage` trait、`MessageIntent`、`Route`、`Capability`、`State`、`OverView`、`Checkpoint`、`CheckpointRule` — 在 `crates/arf-core/src/` |
| 6.2 | Response 协议 | `Response::Done(Value)` 单形态；引擎 park 逻辑（Query 等全部、Command 不入队） |
| 6.3 | Engine 骨架 | `Engine` struct、AgentConfig、State 所有权、4 状态机、bus.connect |
| 6.4 | ReAct 主循环 | 5 个 Checkpoint 位置、fixed ModelCall↔ToolExec 循环、终止判断 |
| 6.5 | Checkpoint 系统 | 规则注册、when/build 调用顺序、intent 决定的 park 行为 |
| 6.6 | 等待队列 + Park/Resume | WaitEvent + PendingMessageWait、correlation_id 匹配、expected_receivers 计算、event strategy 触发、持久化 |
| 6.7 | Route 解析 | BusGraph 查询、Strict/Discovery 转换、多 receiver park 协调 |
| 6.8 | EngineBuilder API | `crates/arf-agent/src/builder.rs`、`NodeBinding`、标准 CheckpointRule 构造器 |
| 6.9 | 集成测试 | MiniEngine + fixtures + ModelAdapter + McpNode 全链路 |
| 6.10 | Python API | PyO3 绑定 Engine + AgentConfig + EngineBuilder |

## 13. 待澄清 / 留待实现阶段

- `Task` 的具体形态（是否 tool_call 抽象？是否包含 subagent 句柄？）
- Node 订阅 msg_types 的注册机制（filter vs 内部自过滤）
- Engine 启动时如何校验 `AgentConfig` 声明的 node_id / capability 真实存在
- 节点掉线时的 Engine 行为（重试 / park / 失败）
- SessionState 持久化时机

### 13.1 已澄清

- ~~`OverView` 字段的精确计算~~ → 见 §2.4 字段计算策略表。`context_tokens` 来自 API 响应的 `usage.prompt_tokens`；`runtime` 是 active time（processing 状态累计）；`model_context_window` 启动时从 ModelAdapter capabilities 读取

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