# 任务 6.1：核心类型定义

> Phase 6 — Engine 核心实现（§9.B）第一项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §1.1 / §1.5 / §1.6
> 前置：§9.A 全部完成（task 6.0.1 ~ 6.0.5）✅

## 设计思路

在 `arf-core` 中定义 Engine 与节点通信的协议性词汇（protocol-level vocabulary）：
- `ActionMessage` trait + `MessageIntent` enum：所有经过 Bus 的应用层消息契约
- `Route` enum + `Capability` struct：路由决策
- `State` / `OverView`：Engine 持有的 Agent 状态
- `Checkpoint` enum + `CheckpointRule`：副作用触发点

这些类型在 6.2（Response）+ 6.5（Checkpoint 系统）+ 6.6（WaitEvent）+ 6.8（EngineBuilder）会被大量引用。先定义好，§9.B 后续任务才能往前走。

**不实现的部分**：
- 不写 Engine 主体逻辑（6.3 ~ 6.4）
- 不写 ReAct 循环（6.4）
- 不写 ResponseProcessor、OnMemberFailedHandler、Barrieria 协议外的 Bus 端逻辑（6.6 / 6.8 范围内）
- 不写 arf-state crate 的 State 持久化（§7.2 提到的 SessionSnapshot 留到 6.6 / 6.8）

**先实现 2 个具体 Message 类型**：`ModelCall` 和 `ToolExec`——这是设计文档 §3 装配示例里用到的两个，必须有才能让后续 checkpoint 示例编译。

## 代码实现

### `crates/arf-core/src/message.rs`（新建）

```rust
//! Application-level message protocol on top of wire `Message`.
//!
//! `ActionMessage` is the trait every payload-type must implement to ride
//! on the Bus. Built-in messages (`ModelCall`, `ToolExec`) implement it
//! directly; App code defines its own via `impl ActionMessage`.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::NodeId;

#[async_trait]
pub trait ActionMessage: Send + Sync {
    /// Wire-level msg_type for Bus routing (e.g., "model_call", "tool_exec").
    fn msg_type(&self) -> &'static str;

    /// Unique ID for response correlation. App code sets this when constructing;
    /// default `Uuid::new_v4()` is fine for most cases.
    fn correlation_id(&self) -> Uuid { Uuid::new_v4() }

    /// Serialize to wire payload (JSON).
    fn payload(&self) -> serde_json::Value;

    /// Whether this message blocks Engine (Query) or fires-and-forgets (Command).
    /// Query: Engine waits for response, parks ReAct loop.
    /// Command: Engine doesn't wait; receiver processes asynchronously.
    fn intent(&self) -> MessageIntent;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MessageIntent {
    /// Engine must wait for the response (e.g., `ModelCall`, `ToolExec`).
    Query,
    /// Engine doesn't wait; receiver processes asynchronously (e.g., `MemoryOp::extract`).
    Command,
}

// ── Built-in messages ────────────────────────────────────────────────

/// `Engine → ModelAdapter`: invoke an LLM with messages, expecting assistant reply.
///
/// Wire `msg_type`: `"model_call"`. Response: `"model_response"`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelCall {
    pub correlation_id: Uuid,
    pub messages: Vec<crate::ModelMessage>,
    pub tools: Vec<crate::ToolSpec>,
}

#[async_trait]
impl ActionMessage for ModelCall {
    fn msg_type(&self) -> &'static str { "model_call" }
    fn correlation_id(&self) -> Uuid { self.correlation_id }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent { MessageIntent::Query }
}

/// `Engine → ToolNode`: execute a tool call.
///
/// Wire `msg_type`: `"tool_exec"`. Response: `"tool_result"`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolExec {
    pub correlation_id: Uuid,
    pub tool_name: String,
    pub arguments: serde_json::Value,
}

#[async_trait]
impl ActionMessage for ToolExec {
    fn msg_type(&self) -> &'static str { "tool_exec" }
    fn correlation_id(&self) -> Uuid { self.correlation_id }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent { MessageIntent::Query }
}
```

### `crates/arf-core/src/tool.rs`（新建，附 ToolSpec）

```rust
//! ToolSpec — describes a tool for LLM function-calling.

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    /// JSON Schema for the tool's arguments.
    pub parameters: Value,
}
```

### `crates/arf-core/src/route.rs`（新建）

```rust
//! Route — how Engine delivers a message to its receiver.
//!
//! Strict: exact NodeIds (point-to-point).
//! Discovery: capability match (broadcast to all matching Nodes).

use serde::{Deserialize, Serialize};

use crate::NodeId;

/// Capability: AND-matched key/value pairs declared by `Node::info()` capabilities.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Capability {
    pub requirements: Vec<(String, String)>,
}

impl Capability {
    pub fn new(requirements: Vec<(String, String)>) -> Self {
        Self { requirements }
    }
    /// Single-key/value convenience constructor.
    pub fn one(key: impl Into<String>, value: impl Into<String>) -> Self {
        Self { requirements: vec![(key.into(), value.into())] }
    }
}

/// Routing decision for a single message type.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Route {
    /// Deliver to exact NodeIds.
    Strict(Vec<NodeId>),
    /// Deliver to all Nodes whose `capabilities` JSON contains the required key/value pairs (AND).
    Discovery(Capability),
}
```

### `crates/arf-core/src/checkpoint.rs`（新建）

```rust
//! Checkpoint — fixed injection points in the ReAct loop.
//!
//! 5 invariant positions; rules decide what side-effect message (if any)
//! to inject at each checkpoint.

use serde::{Deserialize, Serialize};

/// Where a rule may fire (Phase 6 §1.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Checkpoint {
    BeforeModelCall,
    AfterModelCall,
    BeforeToolExec,
    AfterToolExec,
    RoundEnd,
}

/// Checkpoint rule: 4-tuple (name, trigger, when, build).
///
/// **No route** — routes are single-sourced in `AgentConfig.routes` (Phase 6 §2.P3).
/// When `when(state) == true`, Engine calls `build(state)` and dispatches the returned
/// message via the route declared for that message's `msg_type`.
///
/// `when` and `build` closures share the same `'a` lifetime parameter via HRTB.
#[derive(Clone)]
pub struct CheckpointRule {
    pub name: String,
    pub trigger: Checkpoint,
    /// Returns true if rule fires.
    pub when: Box<dyn for<'a> Fn(&'a State) -> bool + Send + Sync>,
    /// Constructs the side-effect message from state.
    pub build: Box<dyn for<'a> Fn(&'a State) -> Box<dyn ActionMessage> + Send + Sync>,
}
```

注意：在这个 draft 里 `build` returns `Box<dyn ActionMessage>`，但 trait `ActionMessage` 不带 `'a` lifetime，所以 HRTB 不需要复杂形式。我们会调整。

### `crates/arf-core/src/state.rs`（新建）

```rust
//! State — Engine-owned Agent state (Phase 6 §1.6).
//!
//! App holds `State` and lends `&mut` to `Engine.run()`. Persistence
//! is App's concern (snapshot via `Engine.snapshot()`, restore via `Engine.restore()`).

use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::{ModelMessage, WaitEvent};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OverView {
    pub round_count: usize,
    pub turn_count: usize,
    /// Most recent LLM-reported prompt token count; `0` before first model_call.
    pub context_tokens: usize,
    pub model_context_window: usize,
    /// Cumulative active time in `processing` state.
    pub runtime: Duration,
    pub last_user_message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct State {
    pub messages: Vec<ModelMessage>,
    pub over_view: OverView,
    pub wait_events: Vec<WaitEvent>,
}

impl Default for State {
    fn default() -> Self {
        Self {
            messages: Vec::new(),
            over_view: OverView::default(),
            wait_events: Vec::new(),
        }
    }
}

impl State {
    pub fn new() -> Self { Self::default() }
}
```

### `crates/arf-core/src/wait_event.rs`（新建）

```rust
//! WaitEvent — pending message group (Phase 6 §1.7 / §2.P4).
//!
//! One WaitEvent awaits 1+ messages sharing a `correlation_id`. Created
//! per publish; removed when WaitStrategy triggers.

use serde::{Deserialize, Serialize};
use std::time::Instant;
use uuid::Uuid;

use crate::Message;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum WaitStrategy {
    /// Fire when all members respond.
    All,
    /// Fire as soon as any one member responds; discard the rest.
    Any,
    /// Fire when N members respond.
    Count(u32),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WaitEvent {
    pub id: Uuid,
    pub correlation_id: Uuid,
    pub strategy: WaitStrategy,
    pub created_at: Instant,
    /// Expected receivers (Strict count or Discovery result snapshot).
    pub expected: usize,
}
```

注意 `Instant` 不支持 serde——需要用 `created_at: std::time::SystemTime` 或自定义 serializer。我们用 `SystemTime`。

```rust
pub created_at: SystemTime,
```

序列化需要 `#[serde(with = "...")]`，或直接用 `u64` 毫秒时间戳。

简化为：
```rust
pub created_at_ms: u64,
```
手动从 Instant 转换。

### `crates/arf-core/src/lib.rs` 更新模块声明

```rust
pub mod node;
pub mod message;
pub mod tool;
pub mod route;
pub mod checkpoint;
pub mod state;
pub mod wait_event;
```

## 测试

### arf-core 内各模块独立测试

**ActionMessage trait** (5 tests):
- ModelCall.msg_type() == "model_call"
- ModelCall.intent() == Query
- ModelCall.correlation_id round-trip 通过 serde
- ToolExec.msg_type() == "tool_exec"
- ToolExec.intent() == Query

**Route** (4 tests):
- Strict 序列化
- Discovery 序列化
- Capability::one() 构造简单 kv
- Capability requirements 边界

**CheckpointRule** (3 tests):
- Checkpoint::all 5 变体存在
- CheckpointRule::new() 编译并 accept HRTB closure
- CheckpointRule 字段访问

**State + OverView** (4 tests):
- State::default() 是空的
- State 可序列化往返
- OverView::default() 全零
- OverView 序列化

**WaitEvent** (3 tests):
- WaitStrategy All/Any/Count 序列化
- WaitEvent 构造
- WaitEvent 序列化（created_at_ms）

**合计 19 个** arf-core 新增。

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test -p arf-core
```

## 测试覆盖摘要

| 模块 | 测试数 | 覆盖角度 |
|------|--------|---------|
| `ActionMessage` | 5 | `[构造][trait][序列化]` |
| `Route`/`Capability` | 4 | `[构造][序列化][边界]` |
| `CheckpointRule` | 3 | `[构造][编译][trait]` |
| `State`/`OverView` | 4 | `[构造][序列化][默认]` |
| `WaitEvent` | 3 | `[构造][序列化]` |
| **合计新增** | **19** | |

---

## 实现后实际发现

### 与初稿的差异

1. **`#[derive(Clone)]` 从 `CheckpointRule` 移除**：`Box<dyn Fn(...)>` 没有 Clone impl，无法 derive Clone。
   修复：去掉 derive，注释说明"如需 clone 包在 `Rc<CheckpointRule>` 里"。

2. **`checkpoint.rs` 不需要 `async_trait`**：初稿 import 了但代码里没用上。
   修复：删除 import。

3. **`WaitEvent.created_at` 从 `Instant` 改成 `created_at_ms: u64`**：`Instant` 不支持 serde；用 `SystemTime::now().duration_since(UNIX_EPOCH).as_millis()` 转毫秒存储。

4. **`tool.rs` 单独成模块**而非放在 `message.rs`：让 `ModelCall.tools` 与 `ToolSpec` 字段含义的"工具描述"语义独立；与 arf-mcp 既有的 Tool 类型区分（不冲突）。

### 实际测试结果

```
cargo test --workspace
...
test result: ok. 154 passed  (arf-core: 134 + 20 new core-type tests)
test result: ok. 91  passed  (arf-bus lib)
test result: ok. 14  passed  (arf-bus integration)
... (其他 crate 全部 OK)
0 FAILED
```

20 个新测试覆盖：
- ActionMessage / MessageIntent: 5（msg_type / intent / serde）
- Route / Capability: 4（构造 / 序列化 / 边界）
- CheckpointRule / Checkpoint: 3（5 variant + HRTB compile + fires/build 调用）
- State / OverView: 4（默认 / helpers / 序列化 / context_utilization）
- WaitEvent / WaitStrategy: 3（variant / 构造 / 序列化）
- ToolSpec: 1（构造 + 序列化）

### 任务输出总结

`crates/arf-core/src/` 新增 6 个文件：
- `message.rs` — ActionMessage trait + MessageIntent + ModelCall + ToolExec（已 `pub use` 到 lib.rs）
- `tool.rs` — ToolSpec（已 `pub use`）
- `route.rs` — Route + Capability（已 `pub use`）
- `checkpoint.rs` — Checkpoint enum + CheckpointRule struct + new()（已 `pub use`）
- `state.rs` — State + OverView + helpers（已 `pub use`）
- `wait_event.rs` — WaitEvent + WaitStrategy（已 `pub use`）

为后续 §9.B 任务铺平道路：
- **6.5 Checkpoint 系统** 可以直接用 `CheckpointRule` + `Checkpoint`
- **6.3 Engine 骨架** 可以直接用 `ActionMessage`、`ModelCall`、`ToolExec`
- **6.6 WaitEvent** 可以直接用 `WaitEvent` 结构

### 下一步：6.2

接下来添加 `Response` enum（`Response::Done(Value)` 单形态）+ `ResponseProcessor` trait（按 response msg_type dispatch）。ResponseProcessor 在 6.5 Checkpoint 系统中会被实际使用，所以 6.2 完成后再做 6.3 / 6.4 / 6.5。