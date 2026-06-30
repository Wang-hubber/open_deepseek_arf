# 任务 6.6：WaitEvent + Park/Resume

> Phase 6 — Engine 核心实现（§9.B）第六项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §1.7 / §2.P4 / §3.3 / §5.6
> 前置：`task-6.5-checkpoint-system` ✅

## 设计思路

把 6.5 的 `response_waits: HashMap<Uuid, oneshot::Sender>`（实际从未被消费）替换成基于 `WaitEvent` 的统一等待队列：每次 Engine 向 Bus publish 一条 Query intent 消息时，在 `State.wait_events` 里登记一个 `WaitEvent`（含 correlation_id、strategy、expected receivers 数），然后调用 `wait_for_strategy` 循环 poll `handle.recv()`，按 correlation_id 匹配响应、按 strategy（All / Any / Count(n)）决定何时触发。

### 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| WaitEvent 位置 | `State.wait_events: Vec<WaitEvent>`（§1.7 已定义） | 持久化时跟随 State；Engine 仅追加/移除 |
| Strategy 默认值 | `WaitStrategy::All(expected)` | 6.6 单消息 + Strict 单 receiver 等价；Any/Count(n) 用于 Discovery 多 receiver 场景 |
| expected 计算 | `recipients.len()` for Strict；Discovery 实时查 graph 后取 len | 在 publish 前确定，避免 publish 后 receiver 上下线带来的计数漂移 |
| Cancel 处理 | 每次 loop entry + recv 后 check；触发时 retain 把 event 移除 | 与 6.4 send_and_await 一致 |
| 串行 publish-and-wait | 仅维护一个 active event（handle.recv 单消费者模型） | 6.6 不引入 mpsc buffer；多 active 留待 6.x |
| response_waits HashMap | 删除（6.5 验证：未被消费，dead code） | 统一到 WaitEvent |
| Partial response | Strategy::Any 触发时丢弃后续消息（已 recv 的收到 vec，未 recv 的留 bus 流里被后续 wait 处理） | §1.7 "discard the rest" |
| 持久化 | State.wait_events 已 Serialize，App 通过 Engine.snapshot/restore 处理 | 6.6 不实现 snapshot；6.9 集成测试覆盖 |

### 不在 6.6 范围（推迟到后续 task）

- Multiple active WaitEvents 并发（6.x；6.6 串行单 active）
- WaitEvent 持久化到磁盘（6.9 集成测试）
- FailedReason / node_offline 触发 WaitStrategy（6.8 OnMemberFailedHandler）
- Heartbeat-based 自动 fail（6.x）

### 关键既有材料（6.5 已实现）

- `WaitEvent` / `WaitStrategy`（`crates/arf-core/src/wait_event.rs`）
- `State.wait_events: Vec<WaitEvent>`（`crates/arf-core/src/state.rs`）
- `Engine.response_waits: Arc<Mutex<HashMap<Uuid, oneshot::Sender>>>`（待删除）
- `Engine.wait_for_response_matching(cid, expected_response_types)`（待替换为 wait_for_strategy）

## 代码实现

### `crates/arf-engine/src/engine.rs` 改动

**1. 删除 `response_waits` 字段**：

```rust
pub struct Engine {
    config: AgentConfig,
    agent_id: NodeId,
    handle: NodeHandle,
    primary_bus: Arc<arf_bus::Bus>,
    // 6.6 删除：
    // response_waits: Arc<Mutex<HashMap<Uuid, oneshot::Sender<serde_json::Value>>>>,
    system_prompt: String,
}
```

**2. `send_and_await` 改为 register WaitEvent + wait_for_strategy**：

```rust
async fn send_and_await(
    &mut self,
    state: &mut State,
    cid: Uuid,
    msg: Message,
    cancel: CancellationToken,
) -> Result<Message, RunError> {
    if cancel.is_cancelled() {
        return Err(RunError::Stopped);
    }
    let response_msg_type = response_msg_type_for(
        msg.msg_type.split('/').next().unwrap_or(&msg.msg_type)
    ).unwrap_or_else(|| format!("{}_result", msg.msg_type));

    // Strict route for Engine's own ModelCall/ToolExec: recipients vec from msg.to
    let expected = msg.to.len().max(1);  // broadcast (to=[]) → 1 expected

    let event = WaitEvent::new(cid, WaitStrategy::All, expected);
    let event_id = event.id;
    state.wait_events.push(event);

    if let Err(e) = self.handle.send_message(msg).await {
        state.wait_events.retain(|e| e.id != event_id);
        return Err(RunError::Bus(e));
    }

    // Wait for first matching response (model_call→model_response, etc.)
    let mut responses = self.wait_for_strategy(
        state, event_id, &[response_msg_type.as_str()], cancel
    ).await?;
    Ok(responses.remove(0))
}
```

**3. `publish_and_await_query` 同样用 WaitEvent + strategy**：

```rust
async fn publish_and_await_query(
    &mut self,
    state: &mut State,
    msg: &dyn ActionMessage,
    recipients: Vec<NodeId>,
    strategy: WaitStrategy,
    cancel: CancellationToken,
) -> Result<Vec<Message>, RunError> {
    if cancel.is_cancelled() {
        return Err(RunError::Stopped);
    }
    let cid = msg.correlation_id();
    let response_msg_type = response_msg_type_for(msg.msg_type())
        .unwrap_or_else(|| format!("{}_result", msg.msg_type()));

    let event = WaitEvent::new(cid, strategy, recipients.len());
    let event_id = event.id;
    state.wait_events.push(event);

    let wire = Message::with_from_bus(
        msg.msg_type().to_string(),
        self.agent_id.clone(),
        recipients,
        msg.payload(),
        self.handle.primary_bus_id(),
    );
    if let Err(e) = self.handle.send_message(wire).await {
        state.wait_events.retain(|e| e.id != event_id);
        return Err(RunError::Bus(e));
    }

    self.wait_for_strategy(state, event_id, &[response_msg_type.as_str()], cancel).await
}
```

**4. `wait_for_strategy` 新增**：

```rust
/// Loop on handle.recv; accumulate responses matching the WaitEvent's cid
/// until the configured strategy triggers.
///
/// - All: trigger when received.len() == event.expected
/// - Any: trigger on first response (discard rest)
/// - Count(n): trigger when received.len() >= n
///
/// Cancels: retain-removes the event; returns Err(Stopped).
async fn wait_for_strategy(
    &mut self,
    state: &mut State,
    event_id: Uuid,
    expected_response_types: &[&str],
    cancel: CancellationToken,
) -> Result<Vec<Message>, RunError> {
    let mut received = Vec::new();
    loop {
        if cancel.is_cancelled() {
            state.wait_events.retain(|e| e.id != event_id);
            return Err(RunError::Stopped);
        }

        let msg = self.handle.recv().await.map_err(|_| {
            RunError::Internal("handle closed".into())
        })?;

        // Look up event (may have been removed by another path).
        let event_info = state.wait_events.iter()
            .find(|e| e.id == event_id)
            .map(|e| (e.correlation_id, e.strategy, e.expected));
        let (our_cid, strategy, expected) = match event_info {
            Some(x) => x,
            None => continue,
        };

        // Filter by msg_type + cid.
        if !expected_response_types.contains(&msg.msg_type.as_str()) {
            continue;
        }
        let msg_cid = msg.payload
            .get("correlation_id")
            .and_then(|v| v.as_str())
            .and_then(|s| Uuid::parse_str(s).ok());
        if msg_cid != Some(our_cid) {
            continue;
        }

        received.push(msg);

        let triggered = match strategy {
            WaitStrategy::All => received.len() >= expected,
            WaitStrategy::Any => true,
            WaitStrategy::Count(n) => received.len() >= n as usize,
        };

        if triggered {
            state.wait_events.retain(|e| e.id != event_id);
            return Ok(received);
        }
    }
}
```

**5. `evaluate_and_dispatch` 传递 `&mut state` 到 publish_and_await_query**：

```rust
async fn evaluate_and_dispatch(
    &mut self,
    state: &mut State,
    trigger: Checkpoint,
    cancel: CancellationToken,
) -> Result<(), RunError> {
    // ... (evaluate 不变)
    for cm in built {
        match cm.msg.intent() {
            MessageIntent::Query => {
                // 默认 All strategy；6.8 暴露 builder 让 App 配置
                self.publish_and_await_query(
                    state,
                    cm.msg.as_ref(),
                    cm.recipients,
                    WaitStrategy::All,
                    cancel.clone(),
                ).await?;
            }
            MessageIntent::Command => {
                self.publish_only_command(cm.msg.as_ref(), cm.recipients).await?;
            }
        }
    }
    Ok(())
}
```

### `crates/arf-engine/src/error.rs` 改动

不需要新变体。`RunError::Stopped` 复用。

### `crates/arf-engine/src/lib.rs` 改动

re-export WaitStrategy：
```rust
pub use arf_core::WaitStrategy;
```

## 测试

`crates/arf-engine/src/tests.rs` 加 6.6 章节：

```rust
// ── Phase 6 task 6.6 — WaitEvent + Park/Resume ──

// [构造] WaitEvent 新建 → id 非零，expected 与传入一致
#[test]
fn wait_event_new_initializes_fields() { ... }

// [构造] WaitStrategy::All expected=2 + 2 响应到达 → 触发；少于 2 不触发
#[tokio::test]
async fn wait_strategy_all_triggers_on_full_set() { ... }

// [构造] WaitStrategy::Any + 1 响应到达 → 立即触发
#[tokio::test]
async fn wait_strategy_any_triggers_on_first() { ... }

// [构造] WaitStrategy::Count(n=2) + 3 个 receiver，2 响应后触发
#[tokio::test]
async fn wait_strategy_count_triggers_at_threshold() { ... }

// [trait] WaitStrategy 序列化/反序列化 round-trip
#[test]
fn wait_strategy_serde_roundtrip() { ... }

// [cancel] wait_for_strategy cancel 触发 → RunError::Stopped + event 从 state 移除
#[tokio::test]
async fn wait_strategy_cancel_removes_event_from_state() { ... }

// [覆盖] State.wait_events 序列化包含 WaitEvent id + correlation_id + strategy
#[test]
fn state_serde_includes_wait_events() { ... }

// [兼容] send_and_await 后 state.wait_events 被清空
#[tokio::test]
async fn send_and_await_clears_wait_events() { ... }

// [路径] Discovery 多 receiver：3 节点中 3 个都响应 → All 触发
#[tokio::test]
async fn discovery_multi_receiver_all_responses_collected() { ... }
```

9 个测试；与 6.5 的 16 个 + 6.4 的 4 个 + 6.3 的 11 个 + 6.2 的 4 个 + 6.1 的核心类型测试一起，6.6 后 engine 总计 ~44 个测试。

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

## 测试覆盖摘要

| 模块 | 测试数 |
|------|--------|
| arf-engine WaitEvent / WaitStrategy / wait_for_strategy | 9 |
| arf-engine CheckpointRule / Checkpoint（已存在） | 3 |
| **合计** | **12**（其中 9 个新增） |

---

## 实现后实际发现

### 与初稿的差异

1. **`send_and_await` 仍返回 `Result<Message, ...>`**（单条响应给 model/tool turn 用）；`publish_and_await_query` 返回 `Result<Vec<Message>, ...>`。初稿统一为 Message，实测两种调用方对响应数量需求不同（model_call 单响应 vs CheckpointRule 多 receiver）。
2. **`expected` 计算**：初稿用 recipients.len()；实测 broadcast（to=[]）必须 fallback 到 1。`send_and_await` 通过 `msg.to.len().is_empty() → 1 else msg.to.len()`；`publish_and_await_query` 用 `recipients.len().max(1)`。
3. **`response_waits` 完全删除**：6.5 验证 oneshot::Sender 实际未被任何代码消费（wait_for_response_matching 直接 poll handle.recv），删除后无功能损失。
4. **`wait_for_strategy` 必须 select! race cancel**：初稿仅在循环顶部 check cancel。实测：`handle.recv()` 会永久 block 若无响应，cancel 在 recv 返回前不会被观察到。修复：用 `tokio::select! { biased; _ = cancel.cancelled() => ...; res = self.handle.recv() => ... }`。
5. **Discovery 多 receiver 测试需模拟 N 个响应**：Discovery route 解析为 N 个 NodeId，但 bus 只 broadcast 一次（每个 node 各自通过 filter 收到）。测试中要手动让 responder 发 N 个响应才能覆盖 All strategy。

### 实现期间 bug

1. **`_` 不能用于 expression-only 上下文**：`let _ = expr; Ok(_)` 不行，需命名：`let responses = expr; Ok(responses)`。
2. **send_message 失败时 event 未清理**：`state.wait_events.retain(|e| e.id != event_id)` 必须在 send_message 失败分支也执行，否则下次 run 残留。
3. **Discovery 测试 timeout**：测试只设 cp_query responder，没设 model_call responder；checkpoint 通过后 engine 正常发 model_call，没人响应就 hang。修复：测试加 `spawn_model_responder`。

### 实际测试结果

```
cargo test --workspace
test result: ok. 52 passed; 0 failed
test result: ok. 91 passed; 0 failed
test result: ok. 14 passed; 0 failed
test result: ok. 161 passed; 0 failed
test result: ok. 41 passed; 0 failed  (arf-engine: 6.6 新增 9 个测试 → 累计 41)
test result: ok. 204 passed; 0 failed
test result: ok. 12 passed; 0 failed
test result: ok. 19 passed; 0 failed
test result: ok. 70 passed; 0 failed
合计 664 passed; 0 failed
```

### 6.6 输出

- `crates/arf-engine/src/engine.rs` 扩展：
  - 删除 `response_waits` 字段
  - 新增 `wait_for_strategy(state, event_id, expected_response_types, cancel)` 方法
  - `send_and_await` 接受 `&mut State` 参数，注册 WaitEvent + 调 wait_for_strategy
  - `publish_and_await_query` 接受 `&mut State` 参数 + `WaitStrategy`，同上
  - `do_model_turn` / `do_tool_turn` 传递 `state` 到 `send_and_await`
  - `evaluate_and_dispatch` 传递 `&mut state` 到 `publish_and_await_query`
- `crates/arf-engine/src/lib.rs` re-export `WaitStrategy`

### 下一步：6.7

**6.7 DiscoveryCache**：把 bus.graph() 的 Capability 解析结果缓存到 Engine；收到 `node_online` / `node_offline` 事件时失效对应 Capability 的缓存条目。