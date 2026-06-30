# 任务 6.5：Checkpoint 系统

> Phase 6 — Engine 核心实现（§9.B）第五项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §1.5 / §2.P3 / §3.2 / §3.3 / §6.1
> 前置：`task-6.4-react-loop` ✅

## 设计思路

在 6.4 已经实现的 ReAct 主循环基础上，插入 5 个固定 Checkpoint 位置（BeforeModelCall / AfterModelCall / BeforeToolExec / AfterToolExec / RoundEnd），让 Engine 在每个位置评估注册的所有 `CheckpointRule`：规则 `when` 为 true 时 `build` 出 `ActionMessage`，Engine 按 msg_type 查 `AgentConfig.routes` 投递，按 `msg.intent()` 决定 park 等响应（Query）还是 fire-and-forget（Command）。

### 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 5 个 Checkpoint 插入位置 | BeforeModelCall 在 publish ModelCall 前；AfterModelCall 在 push assistant 后；BeforeToolExec/AfterToolExec 同理；RoundEnd 在准备 return 前 | §3.2 流程图明示 |
| Rule 调用顺序 | 按注册顺序（`for rule in rules.iter().filter(r => r.trigger == cp)`）；同名重复由 6.3 build() fail-fast 拦截 | 简单 + 可预测 |
| Route 单一源 | msg.msg_type() 必然在 AgentConfig.routes，否则 RunError::UndeclaredMsgType | §1.5 2026-06-30 决议；避免 build 出来的 msg 无投递路径 |
| Discovery 解析 | 每次 checkpoint 评估时 `bus.graph()` 查 Capability 匹配的 Node；**无缓存**（6.7 加 DiscoveryCache） | 6.5 不优化，实现优先 |
| Query intent park | 复用 6.4 `send_and_await`：register wait by correlation_id + send + recv filter by cid & response msg_type | 与 model_call/tool_exec 模式对齐 |
| Command intent | 仅 send（不 register wait，不 await） | §1.5 2026-06-30 决议："Command 语义：Engine 不等" |
| 响应 msg_type 映射 | 用 `response_msg_type_for(msg_type)`：`model_call→model_response`、`tool_exec→tool_result`、`memory_op→memory_op_result`、其他 App 类型→`<msg_type>_result` | 与 6.4 line 330-355 + §3.3 一致 |
| Build 出 ModelCall/ToolExec 怎么办 | 允许：CheckpointRule 可以并行触发额外 ModelCall（如子查询）。但 6.5 简单模式：单 message 串行 publish+await；多消息并行等 WaitEvent（6.6） | YAGNI |
| 评估时机 | 在主循环显式 `evaluate_checkpoint(Checkpoint::BeforeModelCall, state)`；不依赖 Bus lifecycle | Engine 控制时序；确定性 |
| cancel 注入 | evaluate_checkpoint 入口 + send 之前 + await 之前三处检查 cancel | 与 6.4 `send_and_await` 一致 |

### 不在 6.5 范围（推迟到后续 task）

- WaitEvent 多消息队列 + event strategy 触发（6.6）
- DiscoveryCache（6.7）
- EngineBuilder 标准构造器 `every_n_rounds` / `when_context_over` / ResponseProcessor 默认 dispatch / OnMemberFailedHandler（6.8）
- 多 Bus 跨域 CheckpointRule（6.11 MCP facade + 6.12 App-level Recovery）
- Python 绑定（6.10）

### 关键既有材料（6.4 已实现）

- `Engine.run()` 主循环 + `do_model_turn` + `do_tool_turn`（`engine.rs`）
- `AgentConfig.checkpoint_rules: Vec<CheckpointRule>`（`config.rs`）
- `CheckpointRule.fires() / build_msg() / msg_type()` helper（`checkpoint.rs`）
- `EngineBuilder.build()` 校验 + routes 解析 + skill placeholder 替换（`builder.rs`）
- `Message::with_from_bus` + `NodeHandle::send_message` + `engine_response_types` filter

## 代码实现

### `crates/arf-engine/src/checkpoint.rs`（新建）

把 evaluation + dispatch 集中到一个新文件，避免 `engine.rs` 进一步膨胀：

```rust
//! Checkpoint 评估与 dispatch（Phase 6 §2.P3 / task-6.5）。

use arf_bus::NodeHandle;
use arf_core::{
    ActionMessage, Capability, Checkpoint, CheckpointRule, Message,
    MessageFilter, MessageIntent, NodeId, NodeInfo, Route, State, ToMatch,
};
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use tokio_util::sync::CancellationToken;

use crate::config::AgentConfig;
use crate::error::RunError;

/// 一条 Checkpoint 的评估结果。
pub struct CheckpointDispatch<'a> {
    pub trigger: Checkpoint,
    /// checkpoint 触发的所有 msg（按注册顺序）
    pub messages: Vec<(Box<dyn ActionMessage>, Vec<NodeId>)>,
    _phantom: std::marker::PhantomData<&'a ()>,
}

/// 评估一个 Checkpoint 位置：迭代所有规则，when=true 时调用 build，
/// 按 msg_type 在 AgentConfig.routes 中查找 Route 解析为 NodeId 列表。
///
/// 返回 (msg, recipient_ids) 列表，**不发送**——调用方按 intent 分发：
///   - Query intent：Engine.park_and_await
///   - Command intent：Engine.send_fire_forget
pub fn evaluate<'a>(
    trigger: Checkpoint,
    rules: &'a [CheckpointRule],
    config: &AgentConfig,
    bus: &Arc<arf_bus::Bus>,
) -> Result<Vec<(Box<dyn ActionMessage>, Vec<NodeId>)>, RunError> {
    let graph = bus.graph();
    let mut out = Vec::new();
    for rule in rules {
        if rule.trigger != trigger {
            continue;
        }
        if !rule.fires(/* state!  */) { return Err(RunError::Internal("fires takes &State".into())); }
        // 注：fires 实际签名是 &State，evaluate 必须 inline 闭包；此处为简化为可读伪代码，
        // 实现时把 evaluate 改为内联（或 trait 方法），非独立函数。
        unimplemented!()
    }
    Ok(out)
}

/// 把 Route + Bus graph 解析为 recipient NodeIds。
///
/// - Strict: 直接返回 list（不做 graph 查询；但若某 NodeId 不在线仍会出现在结果里——发送时 bus.send 会拒）
/// - Discovery: 遍历 graph nodes，AND-match Capability requirements
pub fn resolve_route(route: &Route, graph: &[NodeInfo]) -> Vec<NodeId> {
    match route {
        Route::Strict(ids) => ids.clone(),
        Route::Discovery(cap) => graph.iter()
            .filter(|n| capability_matches(n, cap))
            .map(|n| n.node_id.clone())
            .collect(),
    }
}

fn capability_matches(node: &NodeInfo, cap: &Capability) -> bool {
    cap.requirements.iter().all(|(k, v)| {
        node.capabilities.get(k).and_then(|x| x.as_str()) == Some(v.as_str())
    })
}
```

> 上面的 evaluate 走伪代码是为了说明。**实际实现** 改成 `pub fn evaluate(state: &State, trigger: Checkpoint, rules: &[CheckpointRule], config: &AgentConfig, bus: &Arc<arf_bus::Bus>) -> Result<...>`，因为 `CheckpointRule.fires()` / `build_msg()` 都接受 `&State`。下面 §actual 给出真实代码。

### `crates/arf-engine/src/engine.rs` 改动

主循环插入 5 处 `evaluate_checkpoint(...)` 调用 + 处理 Query intent 的 park + Command intent 的 send-only：

```rust
//! Engine — ReAct 循环 actor（Phase 6 §3 / §6.5）。

// use statements ...（保留 6.4 的）

use crate::checkpoint as cp_eval;
use crate::config::AgentConfig;
use crate::error::{BuildError, RunError};

impl Engine {
    pub async fn run(
        &mut self,
        state: &mut State,
        user_input: String,
        cancel: CancellationToken,
    ) -> Result<String, RunError> {
        self.prepare_round(state, &user_input);

        loop {
            if cancel.is_cancelled() {
                return Err(RunError::Stopped);
            }
            if state.over_view.turn_count as u32 >= self.config.max_turns {
                return Err(RunError::MaxTurnsExceeded { max_turns: self.config.max_turns });
            }

            // ── Checkpoint::BeforeModelCall ──────────────────────────
            // 评估所有 trigger=BeforeModelCall 的 CheckpointRule
            self.evaluate_and_dispatch(
                state, Checkpoint::BeforeModelCall, cancel.clone()
            ).await?;
            if state.over_view.turn_count as u32 >= self.config.max_turns {
                return Err(RunError::MaxTurnsExceeded { max_turns: self.config.max_turns });
            }

            // 1 model turn (existing 6.4 logic)
            let (content, tool_calls) = self.do_model_turn(state, cancel.clone()).await?;
            if cancel.is_cancelled() {
                return Err(RunError::Stopped);
            }

            // ── Checkpoint::AfterModelCall ───────────────────────────
            self.evaluate_and_dispatch(
                state, Checkpoint::AfterModelCall, cancel.clone()
            ).await?;

            if state.over_view.turn_count as u32 >= self.config.max_turns {
                return Err(RunError::MaxTurnsExceeded { max: ... });
            }

            // 终止：纯文本
            if tool_calls.is_empty() {
                // ── Checkpoint::RoundEnd ──────────────────────────────
                self.evaluate_and_dispatch(
                    state, Checkpoint::RoundEnd, cancel.clone()
                ).await?;
                return Ok(content);
            }

            // tool_exec turns
            for tc in tool_calls {
                if cancel.is_cancelled() {
                    return Err(RunError::Stopped);
                }

                // ── Checkpoint::BeforeToolExec ───────────────────────
                self.evaluate_and_dispatch(
                    state, Checkpoint::BeforeToolExec, cancel.clone()
                ).await?;

                self.do_tool_turn(state, tc, cancel.clone()).await?;

                // ── Checkpoint::AfterToolExec ────────────────────────
                self.evaluate_and_dispatch(
                    state, Checkpoint::AfterToolExec, cancel.clone()
                ).await?;

                if state.over_view.turn_count as u32 >= self.config.max_turns {
                    return Err(RunError::MaxTurnsExceeded { max_turns: self.config.max_turns });
                }
            }
        }
    }

    /// 评估一个 Checkpoint 位置；按 intent 分发。
    async fn evaluate_and_dispatch(
        &mut self,
        state: &mut State,
        trigger: Checkpoint,
        cancel: CancellationToken,
    ) -> Result<(), RunError> {
        let rules = &self.config.checkpoint_rules;
        if rules.is_empty() {
            return Ok(());
        }

        let graph = self.handle.bus().graph();  // 假设 helper；或 self.handle.primary_bus().graph()
        let graph_nodes = graph.nodes;

        for rule in rules {
            if rule.trigger != trigger {
                continue;
            }
            if !rule.fires(state) {
                continue;
            }
            let msg = rule.build_msg(state);
            let msg_type = msg.msg_type().to_string();

            // Route lookup
            let route = self.config.routes.get(&msg_type).ok_or_else(|| {
                RunError::UndeclaredMsgType { msg_type: msg_type.clone() }
            })?;
            let recipients = cp_eval::resolve_route(route, &graph_nodes);

            // Dispatch by intent
            match msg.intent() {
                MessageIntent::Query => {
                    self.publish_and_await(
                        msg.as_ref(), recipients, cancel.clone()
                    ).await?;
                }
                MessageIntent::Command => {
                    self.publish_only(msg.as_ref(), recipients).await?;
                }
            }
            // 注：rules 重新借用 self.config 在 query 分支借用 msg 后冲突——
            // 实际实现时要避免同时借用；下面 §implementation-notes 详述。
        }
        Ok(())
    }

    /// Query intent：publish + register wait + await response by correlation_id。
    async fn publish_and_await(
        &mut self,
        msg: &dyn ActionMessage,
        recipients: Vec<NodeId>,
        cancel: CancellationToken,
    ) -> Result<(), RunError> {
        if cancel.is_cancelled() {
            return Err(RunError::Stopped);
        }
        let cid = msg.correlation_id();
        let to = recipients;
        let wire = Message::with_from_bus(
            msg.msg_type().to_string(),
            self.agent_id.clone(),
            to,
            msg.payload(),
            self.handle.primary_bus_id(),
        );

        // register wait (predict response msg_type)
        let response_msg_type = response_msg_type_for(msg.msg_type());
        let (tx, _rx) = tokio::sync::oneshot::channel();
        self.response_waits.lock().await.insert(cid, (response_msg_type, tx));

        // send
        self.handle.send_message(wire).await?;

        // await
        let response = self.wait_for_response_matching(cid).await?;

        // （可选）调用 ResponseProcessor；6.5 简化：吞下结果，不 push to state。
        // 6.8 会把 response 接入 dispatch 表。
        let _ = response;
        Ok(())
    }

    /// Command intent：publish only（fire-and-forget）。
    async fn publish_only(
        &self,
        msg: &dyn ActionMessage,
        recipients: Vec<NodeId>,
    ) -> Result<(), RunError> {
        let wire = Message::with_from_bus(
            msg.msg_type().to_string(),
            self.agent_id.clone(),
            recipients,
            msg.payload(),
            self.handle.primary_bus_id(),
        );
        self.handle.send_message(wire).await?;
        Ok(())
    }
}
```

### 错误扩展

`crates/arf-engine/src/error.rs` 加：

```rust
#[derive(Debug, Error)]
pub enum RunError {
    // ... existing variants

    /// CheckpointRule.build 出的 msg.msg_type() 不在 AgentConfig.routes 里
    #[error("CheckpointRule 输出的 msg_type '{msg_type}' 未在 AgentConfig.routes 注册")]
    UndeclaredMsgType { msg_type: String },
}
```

### `NodeHandle` 暴露 bus graph（6.5 可能用到）

如果 `NodeHandle` 没有 `bus()` getter，6.5 不能直接查 graph；解法是在 `Engine` struct 多持一个 `Arc<Bus>` 字段（6.3 已有 `buses: Vec<Arc<Bus>>`，但只把 `primary` 传给 NodeHandle，6.5 加 `primary_bus: Arc<Bus>` 字段）：

```rust
pub struct Engine {
    // existing fields
    primary_bus: Arc<arf_bus::Bus>,  // 6.5 加；共享 self.handle 内部同一个 Bus
}
```

实际写法（详见 implementation-notes §N.2）：
```rust
impl Engine {
    async fn checkpoint_graph(&self) -> Vec<NodeInfo> {
        self.primary_bus.graph().nodes
    }
}
```

### implementation-notes（实测常见坑）

**N.1 借用冲突**

`evaluate_and_dispatch` 同时用 `&self.config.checkpoint_rules`（iter）+ `&self.config.routes`（get）+ 调用 `&mut self` 方法（publish_and_await）。需把 config 借用拆段：

```rust
let triggers: Vec<(usize, Checkpoint)> = self.config.checkpoint_rules.iter()
    .enumerate()
    .filter(|(_, r)| r.trigger == trigger)
    .map(|(i, _)| (i, trigger))   // 实际取 index，再用 index 临时 add
    .collect();
// 然后按 index 一个个取 rule，避免长时间持有 self.config 借用
```

更简单的解法：把 `CheckpointRule` 字段 `Clone`（不可，因 Box<dyn Fn>）—— 不可行。

**实际采用**：在 `evaluate_and_dispatch` 入口 clone 出一个 `rules: Vec<&CheckpointRule>` 引用列表，按 index 处理；每条 rule 处理完把 self.config.routes.get 调用与 publish_and_await 串行执行，每次解借用。

**N.2 primary_bus 字段**

6.3 `Engine::new()` 用 `buses[0]` 作为 primary；6.5 加 `primary_bus: buses[0].clone()` 字段，用于查 graph。

**N.3 publish_and_await 的 response_msg_type**

复用 6.4 `response_msg_type_for` helper（line 330–355 in engine.rs）；对 CheckpointRule 路径同样适用。

**N.4 ActionMessage::clone() 不可用**

`Box<dyn ActionMessage>` 不能 Clone——build_msg 返回 owned box。处理时把 msg 用 `&dyn ActionMessage` 引用传给 publish_*，避免 owned 跨阶段。

**N.5 Checkpoint::RoundEnd 触发后立即 return**

设计要求 RoundEnd 在最终 return 前评估。如果 RoundEnd 触发了 Command 的副作用，不需要等响应。如果触发了 Query（如最终"记忆落盘"），也要 await 后再返。

**N.6 CheckpointRule.fires/build_msg 的 closures**

6.5 没问题：`fires(&state)` + `build_msg(&state)` 都接受 &State；state 是 &mut，但 Borrow checker 允许在同一作用域里使用 `&*state`。

## 测试

`crates/arf-engine/src/tests.rs` 加 6.5 章节：

```rust
// ── Phase 6 task 6.5 — Checkpoint 系统 ──

// [构造] BeforeModelCall checkpoint + when=true → 触发 rule.build + publish msg
#[tokio::test]
async fn checkpoint_before_model_call_fires_and_dispatches() { ... }

// [构造] AfterModelCall checkpoint 触发 → 在 model_response 已 push 后才执行
#[tokio::test]
async fn checkpoint_after_model_call_fires_after_push() { ... }

// [构造] BeforeToolExec 在 tool_exec publish 前触发
#[tokio::test]
async fn checkpoint_before_tool_exec_fires_before_publish() { ... }

// [构造] AfterToolExec 在 tool message push 后触发
#[tokio::test]
async fn checkpoint_after_tool_exec_fires_after_push() { ... }

// [构造] RoundEnd 在最终 return 前触发
#[tokio::test]
async fn checkpoint_round_end_fires_before_return() { ... }

// [边界] when=false 不触发 build，也不发送 msg
#[tokio::test]
async fn checkpoint_when_false_skips_dispatch() { ... }

// [边界] 多个 rule 同 trigger 都触发时按注册顺序串行 dispatch
#[tokio::test]
async fn checkpoint_multiple_rules_fire_in_order() { ... }

// [覆盖] 5 个 Checkpoint variant 都被 engine.run 在最小 happy path 中触发
#[tokio::test]
async fn all_five_checkpoints_visited_in_happy_path() { ... }

// [路径] CheckpointRule.build 输出 msg_type 未在 routes 注册 → UndeclaredMsgType
#[tokio::test]
async fn undeclared_msg_type_returns_error() { ... }

// [intent] Query intent 触发 rule → engine park 等响应 → receiver 响应后继续
#[tokio::test]
async fn query_intent_park_and_await_response() { ... }

// [intent] Command intent 触发 rule → engine 不等响应，立即继续
#[tokio::test]
async fn command_intent_fire_and_forget() { ... }

// [方法] Strict route ResolveRoute 返回 route.ids 原样
#[tokio::test]
async fn strict_route_resolve_returns_ids() { ... }

// [方法] Discovery route 用 current bus graph 计算匹配节点
#[tokio::test]
async fn discovery_route_resolve_queries_current_graph() { ... }

// [cancel] evaluate_and_dispatch 中 cancel 触发 → RunError::Stopped（不发送）
#[tokio::test]
async fn checkpoint_eval_returns_stopped_on_cancel() { ... }
```

14 个测试；与 6.4 的 9 个 + 6.3 的 11 个 + 6.2 的 4 个 + 6.1 的核心类型测试一起，6.5 后 engine 总计 ~38 个测试。

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

## 测试覆盖摘要

| 模块 | 测试数 |
|------|--------|
| arf-engine Checkpoint evaluation/dispatch | 14 |
| arf-core CheckpointRule / Checkpoint（已存在） | 3 |
| **合计** | **17**（其中 14 个新增） |

---

## 实现后实际发现（待实测填入）

### 与初稿的差异

1. **借用在 publish_and_await 内部的解决**：实测需要 `take_unchecked` 或放到内部函数；详见 §N.1
2. **WaitEvent 不动**：6.5 单消息 park 模式不创建 WaitEvent，仅在 self.response_waits 注册；多消息时 6.6 才用 Vec<WaitEvent>
3. **DiscoveryCache 不实现**：每次 evaluate 都查 bus.graph()；6.5 acceptance OK；6.7 加缓存

### 实现期间 bug（待填入）

1. ?
2. ?

### 实际测试结果

```
cargo test --workspace
...（待实测）
0 FAILED
```

### 6.5 输出

`crates/arf-engine/src/checkpoint.rs`（新建）：
- `evaluate(state, trigger, rules, config, bus) -> Vec<(msg, recipients)>` —— 纯函数
- `resolve_route(route, graph) -> Vec<NodeId>` —— pure
- `capability_matches(node, cap) -> bool` —— pure helper

`crates/arf-engine/src/engine.rs` 扩展：
- `Engine::evaluate_and_dispatch(state, trigger, cancel)` —— 主循环插入
- `Engine::publish_and_await(msg, recipients, cancel)` —— Query 分发
- `Engine::publish_only(msg, recipients)` —— Command 分发
- `Engine::primary_bus` 字段新增 + 在 6.3 `Engine::new` 赋值

`crates/arf-engine/src/error.rs` 扩展：
- `RunError::UndeclaredMsgType { msg_type }`

### 下一步：6.6

**6.6 WaitEvent + Park/Resume**：把 response_waits 升级成 Vec<WaitEvent>；单消息 publish_and_await 改为 create_wait_event + wait_for_strategy；event strategy = All/Any/Count(n)；partial response 部分填充 received_count。
