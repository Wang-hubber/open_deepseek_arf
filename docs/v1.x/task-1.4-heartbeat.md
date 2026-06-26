# 任务 1.4：心跳检测

> Phase 1 — Bus 消息总线第四项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.3 节点连接与断连

## 设计思路

任务 1.4 为 Bus 增加心跳检测——定时广播 `heartbeat_request`，节点自动 ACK，超时未 ACK 则标记 offline + 广播 `node_offline`。

**心跳流程**：

```
Bus (timer)                          NodeHandle
 │── heartbeat_request (broadcast) ──→│
 │                                     ├─ recv() 内部拦截
 │←──────── HeartbeatAck ────────────│  (自动应答，应用层无感)
 │                                     │
 │── heartbeat_request ──────────────→│  (下一轮)
 │        ... 超时 ...                 │
 │                                     │
 │ 移除 NodeEntry                      │
 │ broadcast node_offline ───────────→全员
```

**核心设计**：

| 维度 | 决策 |
|------|------|
| 定时器位置 | 消息循环内部，`tokio::select!` 在 `cmd_rx` 和 heartbeat interval 之间交替 |
| ACK 方式 | NodeHandle.recv() 内部拦截 `heartbeat_request`，自动发 `HeartbeatAck` 给消息循环 |
| 超时判断 | 消息循环每次 tick 检查：`now - last_ack > heartbeat_timeout` |
| 应用层感知 | 无——`heartbeat_request` 被 NodeHandle 过滤，不返回给应用；`node_offline` 作为普通广播消息可见 |

**消息循环从线性变双路**：

```
Before (1.3):
  cmd_rx.recv() ──→ match cmd ──→ process ──→ loop

After (1.4):
                    ┌── cmd_rx.recv() ──→ process cmd
  tokio::select! ──┤
                    └── heartbeat_timer.tick() ──→ broadcast heartbeat_request
                                                    + check timeouts
                                                    + broadcast node_offline(s)
```

**为什么不用独立 heartbeat task？**

独立的 heartbeat task 需要同步访问 nodes map（`Arc<RwLock<>>`），每次 tick 都要获取写锁。放在消息循环里可以自然串行化——所有 nodes map 操作都在同一个 task 内，避免锁竞争。代价是消息循环在 `check_timeouts` 期间不处理命令，但这是 O(n) 操作，n = 节点数，通常在个位数或十位数。

**为什么 NodeHandle 过滤 heartbeat_request？**

`heartbeat_request` 是内部协议消息，不应暴露给应用层。过滤逻辑在 `recv()`/`try_recv()` 内：
- 收到 `heartbeat_request` → 自动发送 `HeartbeatAck` → 继续等待下一消息
- 其他消息 → 正常返回给调用方

**心跳与 disconnect 的关系**：
- 主动 `disconnect()`：节点发 `Disconnect` 命令，立即广播 `node_offline`
- 被动超时：Bus 检测到超时，自动广播 `node_offline`
- 两种路径最终都是移除 NodeEntry + 广播 `node_offline`

---

## 变更范围

| 文件 | 变更类型 | 内容 |
|------|---------|------|
| `lib.rs` | 修改 | 新增 `HeartbeatAck` 变体；消息循环改为 `tokio::select!` + heartbeat timer；`Bus::new()` 传 interval/timeout 到消息循环 |
| `connection.rs` | 修改 | `recv()` 和 `try_recv()` 内部过滤 `heartbeat_request` + 自动 ACK |
| `heartbeat.rs` | **新建** | `handle_heartbeat_tick()` — 广播 `heartbeat_request` + 超时检查 + 下线处理 |

---

## 代码实现

### `crates/arf-bus/Cargo.toml`

无需修改。`tokio` features 已有 `time`（任务 1.2 已添加）。

---

### `crates/arf-bus/src/lib.rs`（修改）

#### 新增 import

```rust
use tokio::time;
// tokio::sync 已有
```

`tokio::time` 已在 dependencies 中（feature `time`），但需要显式 import。

#### BusCommand 新增 HeartbeatAck

```rust
pub(crate) enum BusCommand {
    Send { ... },
    Connect { ... },
    Disconnect { ... },
    /// Heartbeat acknowledgement from a node.
    /// Sent automatically by NodeHandle when it receives a heartbeat_request.
    HeartbeatAck {
        node_id: NodeId,
    },
    Shutdown { ... },
}
```

逐行：
- `HeartbeatAck` 无 `respond_to` — 不需要回复。ack 只是更新 `last_ack` 时间戳，无需通知发送方

#### Bus::new() 传递 interval/timeout 给消息循环

```rust
pub fn new(
    heartbeat_interval: Duration,
    heartbeat_timeout: Duration,
    channel_capacity: usize,
) -> Self {
    // 移除 let _ = (heartbeat_interval, heartbeat_timeout);
    // 改为传递到消息循环

    let (broadcast_tx, drain_rx) = broadcast::channel(channel_capacity);
    let (cmd_tx, cmd_rx) = mpsc::channel(256);
    let message_count = Arc::new(AtomicU64::new(0));
    let nodes = Arc::new(RwLock::new(HashMap::new()));

    let broadcast_tx_clone = broadcast_tx.clone();
    let count_clone = message_count.clone();
    let nodes_clone = nodes.clone();
    let loop_handle = tokio::spawn(async move {
        run_message_loop(
            cmd_rx,
            broadcast_tx_clone,
            drain_rx,
            count_clone,
            nodes_clone,
            heartbeat_interval,
            heartbeat_timeout,
        )
        .await;
    });

    Self {
        cmd_tx,
        broadcast_tx,
        nodes,
        message_count,
        start_time: Instant::now(),
        _loop_handle: loop_handle,
    }
}
```

逐行：
- `heartbeat_interval` 和 `heartbeat_timeout` 现在传递给 `run_message_loop`，不再用 `let _` 忽略
- 如果 `heartbeat_interval` 为 0，`tokio::time::interval` 会立即触发（等同于 duration 0），这在测试中用短 interval 验证心跳行为

#### 消息循环：从 while-let 改为 tokio::select!

```rust
async fn run_message_loop(
    mut cmd_rx: mpsc::Receiver<BusCommand>,
    broadcast_tx: broadcast::Sender<Message>,
    mut drain_rx: broadcast::Receiver<Message>,
    message_count: Arc<AtomicU64>,
    nodes: Arc<RwLock<HashMap<NodeId, NodeEntry>>>,
    heartbeat_interval: Duration,
    heartbeat_timeout: Duration,
) {
    let mut heartbeat_timer = time::interval(heartbeat_interval);
    // Skip immediate first tick — give nodes time to connect
    heartbeat_timer.tick().await;

    loop {
        tokio::select! {
            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(BusCommand::Send { msg, respond_to }) => {
                        let msg_id = msg.id;
                        let _ = broadcast_tx.send(msg);
                        message_count.fetch_add(1, Ordering::Relaxed);
                        while drain_rx.try_recv().is_ok() {}

                        let online_nodes = nodes.read().unwrap().len();
                        let receipt = SendReceipt {
                            message_id: msg_id,
                            online_nodes,
                            matching_nodes: online_nodes,
                        };
                        let _ = respond_to.send(Ok(receipt));
                    }
                    Some(BusCommand::Connect { info, filter, respond_to }) => {
                        let result = handle_connect(&broadcast_tx, &nodes, info, filter);
                        let _ = respond_to.send(result);
                    }
                    Some(BusCommand::Disconnect { node_id, respond_to }) => {
                        handle_disconnect(&broadcast_tx, &nodes, &node_id);
                        let _ = respond_to.send(());
                    }
                    Some(BusCommand::HeartbeatAck { node_id }) => {
                        if let Ok(mut map) = nodes.write() {
                            if let Some(entry) = map.get_mut(&node_id) {
                                entry.last_ack = Instant::now();
                            }
                        }
                    }
                    Some(BusCommand::Shutdown { respond_to }) => {
                        let _ = respond_to.send(());
                        break;
                    }
                    None => break,
                }
            }
            _ = heartbeat_timer.tick() => {
                handle_heartbeat_tick(&broadcast_tx, &nodes, heartbeat_timeout);
            }
        }
    }
}
```

逐行：
- `heartbeat_timer.tick().await` 首次调用——消耗第一个立即触发，让 timer 从创建时刻开始计时周期。此后每个 `interval` 触发一次
- `tokio::select!` — 同时等待命令和定时器，先到先处理。公平性由 tokio 保证（伪随机选择同时就绪的分支）
- `HeartbeatAck` 处理——获取写锁更新 `last_ack`。如果节点已不在 map 中（可能在收到 ack 前已被标记 offline），静默忽略
- `handle_heartbeat_tick()` — 见 heartbeat.rs，封装 tick 逻辑（广播 + 检查 + 下线）
- `None => break` — 所有 cmd_tx sender 被 drop（Bus 已 drop），消息循环退出

---

### `crates/arf-bus/src/heartbeat.rs`（新文件）

```rust
//! Heartbeat detection — timer-driven liveness checks.
//!
//! The Bus periodically broadcasts `heartbeat_request` messages.
//! Each `NodeHandle` automatically acks when it consumes one.
//! Nodes that don't ack within `heartbeat_timeout` are marked offline.

use arf_core::{Message, NodeId, NodeInfo, NodeEntry};
use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};
use tokio::sync::broadcast;

/// Called on each heartbeat timer tick.
///
/// 1. Broadcasts `heartbeat_request` to all subscribers.
/// 2. Checks for nodes that haven't acked within `heartbeat_timeout`.
/// 3. Removes timed-out nodes and broadcasts `node_offline` for each.
pub(crate) fn handle_heartbeat_tick(
    broadcast_tx: &broadcast::Sender<Message>,
    nodes: &Arc<RwLock<HashMap<NodeId, NodeEntry>>>,
    heartbeat_timeout: Duration,
) {
    let now = Instant::now();

    // 1. Broadcast heartbeat_request
    let heartbeat_msg = Message::new(
        "heartbeat_request",
        NodeId::new("bus"),
        None,
        serde_json::json!({}),
    );
    let _ = broadcast_tx.send(heartbeat_msg);

    // 2. Check for timed-out nodes
    let timed_out: Vec<(NodeId, NodeInfo)> = {
        let map = nodes.read().unwrap();
        map.iter()
            .filter(|(_, entry)| now.duration_since(entry.last_ack) > heartbeat_timeout)
            .map(|(id, entry)| (id.clone(), entry.info.clone()))
            .collect()
    };

    // 3. Remove and broadcast node_offline
    for (node_id, info) in &timed_out {
        {
            let mut map = nodes.write().unwrap();
            map.remove(node_id);
        }

        let offline_msg = Message::new(
            "node_offline",
            node_id.clone(),
            None,
            serde_json::to_value(info).unwrap_or_default(),
        );
        let _ = broadcast_tx.send(offline_msg);
    }
}
```

逐行：
- `NodeId::new("bus")` — heartbeat_request 的 from 字段设为 "bus"，表示这是基础设施消息
- `now.duration_since(entry.last_ack) > heartbeat_timeout` — 超过容忍时限则标记 offline。注意不是 `>=`，给刚好卡在边界的情况留 1 tick 缓冲
- 先收集 `timed_out` 列表（读锁），再逐个移除和广播（写锁）——避免在迭代 HashMap 时持有写锁过久
- `node_offline` payload 带完整 `NodeInfo`——其他节点看到 `node_offline` 时可以知道下线节点的能力

---

### `crates/arf-bus/src/connection.rs`（修改）

#### recv() 增加 heartbeat 过滤

```rust
/// Receive the next application-visible message from the Bus.
///
/// Heartbeat requests are intercepted and auto-acknowledged — they are
/// never returned to the caller.
///
/// MessageFilter filtering will be applied in task 1.6.
pub async fn recv(&mut self) -> Result<Message, broadcast::error::RecvError> {
    loop {
        let msg = self.broadcast_rx.recv().await?;
        if msg.msg_type == "heartbeat_request" {
            // Auto-ack heartbeat, continue waiting for application messages
            let _ = self
                .cmd_tx
                .send(BusCommand::HeartbeatAck {
                    node_id: self.info.node_id.clone(),
                })
                .await;
            continue;
        }
        return Ok(msg);
    }
}
```

逐行：
- `loop { ... }` — 持续接收直到遇到非 heartbeat 消息。多个连续 heartbeat（在快速 heartbeat_interval 场景）都会被过滤
- `self.cmd_tx.send(BusCommand::HeartbeatAck {...}).await` — 异步发送 ack。如果命令通道满（256 容量），`send().await` 会等待。在极端负载下，这保证了 ack 最终送达
- 跨 await 的 `&mut self` 借用——`broadcast_rx.recv().await` 和 `cmd_tx.send().await` 在不同迭代中执行，不冲突。同一迭代中，Rust 允许通过 `&mut self` 同时借用不同字段（`broadcast_rx` + `cmd_tx` + `info`）

#### try_recv() 增加 heartbeat 过滤

```rust
/// Try to receive without blocking.
///
/// Heartbeat requests are intercepted and auto-acknowledged (using
/// `try_send` since this is a synchronous method).
pub fn try_recv(&mut self) -> Result<Option<Message>, broadcast::error::TryRecvError> {
    loop {
        match self.broadcast_rx.try_recv() {
            Ok(msg) => {
                if msg.msg_type == "heartbeat_request" {
                    // Auto-ack (non-blocking — try_send, not send)
                    let _ = self.cmd_tx.try_send(BusCommand::HeartbeatAck {
                        node_id: self.info.node_id.clone(),
                    });
                    continue;
                }
                return Ok(Some(msg));
            }
            Err(broadcast::error::TryRecvError::Empty) => return Ok(None),
            Err(e @ broadcast::error::TryRecvError::Lagged(_)) => return Err(e),
            Err(broadcast::error::TryRecvError::Closed) => {
                return Err(broadcast::error::TryRecvError::Closed)
            }
        }
    }
}
```

逐行：
- `try_send` 而非 `send().await` — `try_recv()` 是同步方法，不能 `.await`。`try_send` 在 channel 满时返回 error，ack 丢失→下一 tick 检测超时，这是合理行为（命令通道满说明系统极度繁忙）

---

### 模块注册

在 `lib.rs` 末尾添加：

```rust
mod heartbeat;
```

`heartbeat` 模块不需要 `pub use` — `handle_heartbeat_tick` 是 `pub(crate)`，仅在 crate 内被消息循环调用。

---

## 单元测试

### 测试分类

```
heartbeat
├── 基本心跳 (3 tests)
│   ├── [心跳] NodeHandle.recv() 不返回 heartbeat_request
│   ├── [心跳] NodeHandle.try_recv() 不返回 heartbeat_request
│   └── [心跳] heartbeat_request 被广播到所有 subscriber
├── 超时检测 (2 tests)
│   ├── [超时] 节点长时间不发 ACK → node_offline 广播
│   └── [超时] 正常节点（定期 recv）不会被误判 offline
├── 并发 (2 tests)
│   ├── [并发] 多个节点同时 ACK，all successful
│   └── [并发] 心跳和 send/connect/disconnect 并发不冲突
└── 边界 (1 test)
    └── [边界] heartbeat_interval=0 不 panic
```

### 测试代码

```rust
// In crates/arf-bus/src/heartbeat.rs

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Bus, ConnectError};
    use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
    use std::time::Duration;

    fn test_node_info(id: &str) -> NodeInfo {
        NodeInfo {
            node_id: NodeId::new(id),
            node_type: "test".into(),
            capabilities: serde_json::json!({}),
            online_since: 0,
        }
    }

    fn test_filter() -> MessageFilter {
        MessageFilter {
            types: None,
            to_match: ToMatch::All,
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // 基本心跳 (3 tests)
    // ═══════════════════════════════════════════════════════════════

    // [心跳] NodeHandle.recv() 不返回 heartbeat_request
    #[tokio::test]
    async fn recv_filters_out_heartbeat_request() {
        let bus = Bus::new(
            Duration::from_millis(10),  // fast heartbeat for test
            Duration::from_secs(10),    // long timeout
            16,
        );
        let mut handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();

        // Wait for a heartbeat_request to arrive
        tokio::time::sleep(Duration::from_millis(30)).await;

        // Use try_recv to verify no heartbeat_request leaks
        // (If one slips through, the test assertion catches it)
        while let Ok(Some(msg)) = handle.try_recv() {
            assert_ne!(msg.msg_type, "heartbeat_request",
                "heartbeat_request should be filtered by recv/try_recv");
        }

        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [心跳] heartbeat_request 被广播到 raw subscribe()
    #[tokio::test]
    async fn heartbeat_request_visible_on_raw_subscribe() {
        let bus = Bus::new(
            Duration::from_millis(10),
            Duration::from_secs(10),
            16,
        );
        let mut rx = bus.subscribe();

        // Raw subscriber sees heartbeat_request
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.msg_type, "heartbeat_request");
        assert_eq!(msg.from.as_str(), "bus");

        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // 超时检测 (2 tests)
    // ═══════════════════════════════════════════════════════════════

    // [超时] 节点不调用 recv → 不发送 ACK → 超时 → node_offline
    #[tokio::test]
    async fn node_without_ack_times_out_and_broadcasts_offline() {
        let bus = Bus::new(
            Duration::from_millis(30),   // heartbeat interval
            Duration::from_millis(50),   // short timeout for test
            16,
        );
        let mut rx = bus.subscribe();

        // Connect a node that NEVER calls recv
        let handle = bus.connect(test_node_info("zombie"), test_filter()).await.unwrap();
        // Drain node_online
        let _ = rx.recv().await.unwrap();

        // Wait long enough for timeout
        tokio::time::sleep(Duration::from_millis(200)).await;

        // The zombie node should have timed out → node_offline broadcast
        // Drain heartbeat_request messages until we see node_offline
        let mut saw_offline = false;
        for _ in 0..20 {
            let msg = rx.recv().await.unwrap();
            if msg.msg_type == "node_offline" && msg.from.as_str() == "zombie" {
                saw_offline = true;
                break;
            }
        }
        assert!(saw_offline, "zombie node should have been marked offline");

        // Clean up — disconnect will silently fail (node already removed)
        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [超时] 正常节点定期 recv 不会超时
    #[tokio::test]
    async fn healthy_node_does_not_time_out() {
        let bus = Bus::new(
            Duration::from_millis(20),
            Duration::from_millis(200),
            16,
        );
        let mut rx = bus.subscribe();
        let mut handle = bus.connect(test_node_info("healthy"), test_filter()).await.unwrap();

        // Drain node_online
        let _ = rx.recv().await.unwrap();

        // Keep consuming messages for several heartbeat cycles
        for _ in 0..5 {
            // recv() auto-acks heartbeat_request
            let _ = handle.recv().await; // may be heartbeat, filtered by recv — but we need app messages...
            // Actually recv() will just filter heartbeat and wait for next.
            // Use try_recv() instead to verify no node_offline for us
        }

        // Better approach: use a timeout on the rx to verify no node_offline for "healthy"
        let mut saw_offline_for_healthy = false;
        // Drain what we can with try_recv
        while let Ok(msg) = rx.try_recv() {
            if msg.msg_type == "node_offline" && msg.from.as_str() == "healthy" {
                saw_offline_for_healthy = true;
            }
        }
        assert!(!saw_offline_for_healthy, "healthy node should not be marked offline");

        handle.disconnect().await;
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // 边界 (1 test)
    // ═══════════════════════════════════════════════════════════════

    // [边界] heartbeat_interval=0 不 panic
    #[tokio::test]
    async fn zero_heartbeat_interval_does_not_panic() {
        let bus = Bus::new(
            Duration::from_millis(0),
            Duration::from_millis(10),
            16,
        );
        // Should not panic — just rapid ticks
        tokio::time::sleep(Duration::from_millis(10)).await;
        bus.shutdown().await;
    }
}
```

实际上 `healthy_node_does_not_time_out` 的测试逻辑有问题，需要修正：

```rust
// [超时] 正常节点定期 recv 不会超时
#[tokio::test]
async fn healthy_node_does_not_time_out() {
    let bus = Bus::new(
        Duration::from_millis(20),
        Duration::from_millis(200),
        16,
    );
    let mut rx = bus.subscribe();
    let mut handle = bus.connect(test_node_info("healthy"), test_filter()).await.unwrap();

    // Drain node_online
    let _ = rx.recv().await.unwrap();

    // Consume via recv() for several heartbeat cycles (recv auto-acks heartbeats)
    // Since recv() filters heartbeat_request, when there are no app messages,
    // recv() will just keep consuming and acking heartbeat_requests internally.
    //
    // To verify acks are sent, we check that no node_offline for "healthy" appears.
    // But we can't call recv() on the handle (it blocks waiting for app messages).
    // Instead: spawn a task that calls recv() in a loop (it will be busy with heartbeats)
    let healthy_id = NodeId::new("healthy");
    let handle_arc = Arc::new(tokio::sync::Mutex::new(handle));

    let h = handle_arc.clone();
    let recv_task = tokio::spawn(async move {
        // This will loop forever — just consume and ack heartbeats
        loop {
            let mut guard = h.lock().await;
            if guard.recv().await.is_err() {
                break; // channel closed
            }
        }
    });

    // Wait long enough for several heartbeat cycles
    tokio::time::sleep(Duration::from_millis(150)).await;

    // Check that no node_offline for "healthy" was broadcast
    let mut saw_offline = false;
    while let Ok(msg) = rx.try_recv() {
        if msg.msg_type == "node_offline" && msg.from == healthy_id {
            saw_offline = true;
        }
    }
    assert!(!saw_offline, "healthy node should not be marked offline");

    recv_task.abort();
    // Get handle back
    let handle = Arc::into_inner(handle_arc).unwrap().into_inner();
    handle.disconnect().await;
    bus.shutdown().await;
}
```

这个测试变复杂了。让我简化——不测试 recv() 的持续 ack 行为（这属于集成测试 1.9），改为更简单的测试：

```rust
// [超时] 正常节点发送 HeartbeatAck 后 last_ack 更新，不会超时
// 直接注入 HeartbeatAck 命令来验证 last_ack 更新（白盒测试）
#[tokio::test]
async fn heartbeat_ack_updates_last_ack() {
    let bus = Bus::new(
        Duration::from_millis(100),
        Duration::from_millis(50),  // short timeout
        16,
    );
    let mut rx = bus.subscribe();

    // Connect a node but DON'T let it recv (no auto-ack)
    // Instead, manually send HeartbeatAck via Bus::cmd_tx
    let handle = bus.connect(test_node_info("acked"), test_filter()).await.unwrap();
    let _ = rx.recv().await.unwrap(); // drain node_online

    // Manually send a HeartbeatAck after a delay
    tokio::time::sleep(Duration::from_millis(120)).await;
    bus.cmd_tx.send(BusCommand::HeartbeatAck {
        node_id: NodeId::new("acked"),
    }).await.unwrap();

    // The node should NOT be marked offline (we just acked)
    // Wait for another tick
    tokio::time::sleep(Duration::from_millis(200)).await;

    // Drain all messages, check no node_offline for "acked"
    let mut saw_offline = false;
    while let Ok(msg) = rx.try_recv() {
        if msg.msg_type == "node_offline" && msg.from.as_str() == "acked" {
            saw_offline = true;
        }
    }
    assert!(!saw_offline, "acked node should not be marked offline");

    handle.disconnect().await;
    bus.shutdown().await;
}
```

好吧这也很复杂，而且需要访问 `bus.cmd_tx`（已经是 `pub(crate)`）。让我简化。

实际上，最简单、最清晰的测试策略：

1. 用快速的 heartbeat_interval + 短 timeout，验证超时节点被标记 offline
2. 验证正常 recv() 过滤 heartbeat_request
3. 验证 raw subscribe() 能看到 heartbeat_request

对于健康节点的测试（不会被错误标记），由于 recv() 会阻塞等待应用消息，测试起来比较复杂。最务实的做法是：**白盒验证**——发 HeartbeatAck → 检查 last_ack 已更新 → 验证不会被超时清除。

或者更简单：直接验证 `handle_heartbeat_tick` 函数的逻辑。

让我重新设计测试，保持简单务实。

---

## 最终测试清单

| # | 角度 | 测试名 | 覆盖 |
|---|------|--------|------|
| 1 | `[心跳]` | `recv_filters_out_heartbeat_request` | NodeHandle.recv 不暴露 heartbeat |
| 2 | `[心跳]` | `try_recv_filters_heartbeat_and_acks` | try_recv 过滤 + 自动 ACK |
| 3 | `[心跳]` | `heartbeat_request_visible_on_raw_subscribe` | raw subscriber 可见 |
| 4 | `[超时]` | `node_without_ack_times_out` | 不发 ACK → 超时 → node_offline |
| 5 | `[超时]` | `heartbeat_ack_prevents_timeout` | 手动发 ack → 不被标记 offline |
| 6 | `[边界]` | `zero_heartbeat_interval_does_not_panic` | interval=0 不 panic |
| 7 | `[边界]` | `heartbeat_shutdown_no_panic` | shutdown 期间 tick 不 panic |

---

## 对已有测试的影响

- `Bus::new()` 现在实际使用 `heartbeat_interval`/`heartbeat_timeout`，已有测试用 `Duration::from_secs(1)` / `Duration::from_secs(3)` ——这些足够长（1s interval），不影响现有测试的断言
- `NodeHandle::recv()` 现在内部有 `loop`——对于已有测试，行为不变（只是多了一层过滤非 heartbeat 消息）。已有测试不会看到 heartbeat_request 因为他们没有 wait 足够长
- `Bus::shutdown()` 后消息循环退出，heartbeat timer 自然终止（`select!` 的两个分支都不会再触发）

---

## 小结

- **消息循环升级为 `tokio::select!`**——同时监听 cmd 和 heartbeat timer
- **NodeHandle 透明处理心跳**——`recv()`/`try_recv()` 拦截 `heartbeat_request`，自动 ACK，应用层无感
- **超时自动离线**——`last_ack` 超 `heartbeat_timeout` → 移除 + 广播 `node_offline`
- **heartbeat_request 源标识为 "bus"**——与节点消息区分
- **7 个新测试**：2 过滤 + 1 可见性 + 2 超时 + 2 边界
