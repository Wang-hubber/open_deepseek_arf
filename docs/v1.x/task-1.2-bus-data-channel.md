# 任务 1.2：Bus 数据通道

> Phase 1 — Bus 消息总线第二项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.1 `arf-core` 共享类型

## 设计思路

任务 1.2 搭建 Bus 的骨架——两条 channel + 一条消息循环。这是整个 Phase 1 的核心基础设施，后续 1.3-1.7 全部在此基础上叠加。

**核心架构：**

```
Node ──send()──→ cmd_tx (mpsc) ──→ 消息循环 ──→ broadcast_tx ──→ subscribe() ──→ Node
                                      │
                                      ├─ 验证消息
                                      ├─ 广播给全员
                                      └─ message_count++
```

**两条 channel 的分工：**

| Channel | 类型 | 方向 | 作用 |
|---------|------|------|------|
| `cmd_tx` / `cmd_rx` | `mpsc` | 多写入 → 单消费 | 节点发送指令给 Bus（send、connect、心跳 ACK） |
| `broadcast_tx` / `broadcast_rx` | `broadcast` | 单写入 → 多消费 | Bus 广播消息给所有订阅者 |

**为什么 Bus 自己持有一个 receiver？**

`tokio::sync::broadcast` 的 `send()` 在所有 receiver 都 drop 时会返回 error。Bus 持有一个 dummy receiver（`_bus_rx`），保证 broadcast channel 永远存活——对应 CAN 模型中"线缆永远有电"。节点可以自由上下线，Bus 不受影响。

**任务 1.2 的范围：**

- `Bus::new()` — 创建两条 channel + 启动消息循环
- `Bus::send()` — 发消息（经过 mpsc → 消息循环 → broadcast）
- `Bus::subscribe()` — 获取一个 broadcast receiver
- `Bus::shutdown()` — 关闭 Bus
- `Bus::message_count()` / `Bus::uptime_ms()` — 基本监控
- 消息循环 — 收 cmd → 广播 → 计数

**不在 1.2 范围：**
- NodeHandle / connect / disconnect（1.3）
- 心跳检测（1.4）
- SendReceipt / 投递保证（1.5）
- MessageFilter 过滤（1.6）
- BusGraph 健康图（1.7）

---

## 依赖

### `crates/arf-bus/Cargo.toml`

```toml
[package]
name = "arf-bus"
version.workspace = true
edition.workspace = true
license.workspace = true
repository.workspace = true
description = "ARF J-RPC broadcast bus: message routing, node lifecycle, online graph"

[dependencies]
arf-core = { path = "../arf-core" }
tokio = { version = "1", features = ["sync", "rt", "macros"] }
```

逐行：
- `arf-core` — 已有，使用 `Message`、`SendError` 等类型
- `tokio` feature `sync` — `mpsc::channel`、`broadcast::channel`、`oneshot::channel`
- `tokio` feature `rt` — `tokio::spawn` 启动消息循环
- `tokio` feature `macros` — `#[tokio::test]` 异步测试

---

## 代码实现

### `crates/arf-bus/src/lib.rs`

```rust
//! ARF Bus — J-RPC broadcast message bus.
//!
//! All nodes communicate through this bus. It maintains an online node graph,
//! handles node lifecycle (online/offline/heartbeat), and routes messages
//! (broadcast when `to` is empty, directed otherwise).

use arf_core::{Message, SendError};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{broadcast, mpsc, oneshot};

// ═══════════════════════════════════════════════════════════════════
// Bus
// ═══════════════════════════════════════════════════════════════════

/// J-RPC broadcast message bus — CAN model: single wire, all nodes on it.
///
/// Two channels power the Bus:
/// - `cmd_tx` (mpsc): nodes send commands to the bus (send, connect, heartbeat ack)
/// - `broadcast_tx` (broadcast): bus broadcasts messages to all subscribers
///
/// The Bus holds a dummy `broadcast::Receiver` to keep the channel alive,
/// mirroring CAN's "the wire is always powered."
pub struct Bus {
    /// Send commands into the bus message loop.
    cmd_tx: mpsc::Sender<BusCommand>,
    /// Clone of the broadcast sender, used by `subscribe()`.
    broadcast_tx: broadcast::Sender<Message>,
    /// Total messages broadcast since start.
    message_count: Arc<AtomicU64>,
    /// When the bus was created.
    start_time: Instant,
    /// JoinHandle for the message loop task.
    _loop_handle: tokio::task::JoinHandle<()>,
    /// Dummy receiver — keeps the broadcast channel alive when no nodes are connected.
    _bus_rx: broadcast::Receiver<Message>,
}
```

逐行：
- `cmd_tx: mpsc::Sender<BusCommand>` — 命令通道的发送端。外部通过 `send()` 向 Bus 投递命令，Bus 内部消息循环逐一处理。`mpsc` = 多生产者单消费者，多节点同时发消息不会冲突
- `broadcast_tx: broadcast::Sender<Message>` — 广播通道的发送端。消息循环处理完命令后通过它广播给所有订阅者。`broadcast` = 单生产者多消费者，所有节点收到同一份消息的 clone
- `message_count: Arc<AtomicU64>` — 原子计数器，消息循环和 `Bus::message_count()` 共享。用 `AtomicU64` 而非 `Mutex<u64>`，因为只是计数不需要锁
- `start_time: Instant` — 创建时刻，`uptime_ms()` 用它计算运行时间
- `_loop_handle: JoinHandle<()>` — 消息循环的 tokio task 句柄。名前加 `_` 表示不会被主动读取，但持有它防止 task 被 drop 取消
- `_bus_rx: broadcast::Receiver<Message>` — dummy 接收者，保持 broadcast channel 存活

```rust
/// Internal commands sent to the bus message loop.
enum BusCommand {
    /// Send a message to be broadcast.
    Send {
        msg: Message,
        respond_to: oneshot::Sender<Result<(), SendError>>,
    },
    /// Shut down the bus.
    Shutdown {
        respond_to: oneshot::Sender<()>,
    },
}
```

逐行：
- `BusCommand::Send` — 携带 `Message` 和一个 `oneshot::Sender`，消息循环处理完后通过 oneshot 回复结果。`oneshot` = 一次性通道，请求-响应模式
- `BusCommand::Shutdown` — 停止消息循环。`oneshot` 回复让调用方确认 loop 已退出
- 后续任务（1.3、1.4）会新增 `Connect`、`HeartbeatAck` 等变体

```rust
impl Bus {
    /// Create a new Bus with the given configuration.
    ///
    /// - `heartbeat_interval`: interval between heartbeat requests (used in task 1.4)
    /// - `heartbeat_timeout`: how long to wait for a heartbeat ack (used in task 1.4)
    /// - `channel_capacity`: size of the broadcast ring buffer
    pub fn new(
        heartbeat_interval: Duration,
        heartbeat_timeout: Duration,
        channel_capacity: usize,
    ) -> Self {
        let (broadcast_tx, bus_rx) = broadcast::channel(channel_capacity);
        let (cmd_tx, cmd_rx) = mpsc::channel(256);
        let message_count = Arc::new(AtomicU64::new(0));

        let broadcast_tx_clone = broadcast_tx.clone();
        let count_clone = message_count.clone();
        let loop_handle = tokio::spawn(async move {
            run_message_loop(cmd_rx, broadcast_tx_clone, count_clone).await;
        });

        Self {
            cmd_tx,
            broadcast_tx,
            message_count,
            start_time: Instant::now(),
            _loop_handle: loop_handle,
            _bus_rx: bus_rx,
        }
    }
```

逐行：
- `broadcast::channel(channel_capacity)` — 创建广播通道，capacity 即环形缓冲区大小（spec 默认 1024）
- `mpsc::channel(256)` — 命令通道 buffer 256 条，足够缓冲高并发写入
- `broadcast_tx.clone()` — `broadcast::Sender` 是 `Clone` 的，clone 一份给消息循环。Bus 自己保留原始 copy 用于 `subscribe()`
- `count_clone = message_count.clone()` — `Arc::clone()` 增加引用计数，消息循环和 Bus 共享同一个 `AtomicU64`
- `tokio::spawn(async move { ... })` — 在 tokio runtime 上启动消息循环。`async move` 将 `cmd_rx`、`broadcast_tx_clone`、`count_clone` 的所有权移入闭包

```rust
    /// Send a message to be broadcast on the bus.
    ///
    /// Returns `Ok(())` if the message was successfully queued for broadcast.
    /// Returns `Err(SendError::BusClosed)` if the bus has been shut down.
    pub async fn send(&self, msg: Message) -> Result<(), SendError> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx
            .send(BusCommand::Send {
                msg,
                respond_to: tx,
            })
            .await
            .map_err(|_| SendError::BusClosed)?;
        rx.await.map_err(|_| SendError::BusClosed)?
    }
```

逐行：
- `oneshot::channel()` — 创建一次性请求-响应通道
- `cmd_tx.send(...).await` — 向命令通道发送 Send 命令。如果消息循环已退出（Bus 已 shutdown），`cmd_rx` 已 drop，`send()` 返回 error → 转为 `BusClosed`
- `rx.await` — 等待消息循环的回复。如果 oneshot sender 被 drop（消息循环 panic），也返回 `BusClosed`
- 为什么 send 是 `async`？因为需要 `.await` 等待 mpsc 通道有容量和 oneshot 回复

```rust
    /// Subscribe to all broadcast messages on this bus.
    ///
    /// Returns a `broadcast::Receiver` that receives every message
    /// broadcast on the bus. In task 1.3, this will be wrapped in `NodeHandle`.
    pub fn subscribe(&self) -> broadcast::Receiver<Message> {
        self.broadcast_tx.subscribe()
    }

    /// Total number of messages broadcast since the bus started.
    pub fn message_count(&self) -> u64 {
        self.message_count.load(Ordering::Relaxed)
    }

    /// Milliseconds since the bus was created.
    pub fn uptime_ms(&self) -> u64 {
        self.start_time.elapsed().as_millis() as u64
    }

    /// Shut down the bus.
    ///
    /// Drops the message loop task and the dummy receiver,
    /// causing all subsequent `send()` calls to return `BusClosed`.
    pub async fn shutdown(self) {
        let (tx, rx) = oneshot::channel();
        let _ = self.cmd_tx.send(BusCommand::Shutdown { respond_to: tx }).await;
        let _ = rx.await;
        // self is dropped here → _bus_rx dropped → broadcast channel closed
    }
}
```

逐行：
- `subscribe()` — `broadcast::Sender::subscribe()` 创建一个新的 `Receiver`，从调用瞬间开始接收后续消息（不回溯历史）
- `message_count()` — `Ordering::Relaxed` 足够，因为只是计数不需要与其他内存操作同步。性能最优
- `uptime_ms()` — `Instant::elapsed()` 返回自创建以来的 `Duration`，转毫秒。单调时钟，不受系统时间调整影响
- `shutdown()` — 发 Shutdown 命令给消息循环，等待确认。method 签名 `self`（非 `&self`）表示 consume Bus，调用后 Bus 不可再用

```rust
// ═══════════════════════════════════════════════════════════════════
// Message loop
// ═══════════════════════════════════════════════════════════════════

/// Internal message loop — the heart of the Bus.
///
/// Receives commands from `cmd_rx`, processes them, and broadcasts messages
/// through `broadcast_tx`. Runs in a dedicated tokio task.
async fn run_message_loop(
    mut cmd_rx: mpsc::Receiver<BusCommand>,
    broadcast_tx: broadcast::Sender<Message>,
    message_count: Arc<AtomicU64>,
) {
    while let Some(cmd) = cmd_rx.recv().await {
        match cmd {
            BusCommand::Send { msg, respond_to } => {
                // Broadcast to all subscribers (including dummy).
                // Ignore the receiver count — CAN model: fire and forget.
                let _ = broadcast_tx.send(msg);
                message_count.fetch_add(1, Ordering::Relaxed);
                let _ = respond_to.send(Ok(()));
            }
            BusCommand::Shutdown { respond_to } => {
                let _ = respond_to.send(());
                break;
            }
        }
    }
}
```

逐行：
- `while let Some(cmd) = cmd_rx.recv().await` — 循环等待命令。当所有 `cmd_tx` sender 被 drop 时，`recv()` 返回 `None`，循环退出。正常路径通过 `Shutdown` 命令 exit
- `let _ = broadcast_tx.send(msg)` — 忽略返回值（receiver 数量）。CAN 模型：消息发到线上即可，收不收是节点的事
- `message_count.fetch_add(1, Ordering::Relaxed)` — 原子加 1。`Relaxed` 因为计数不参与其他同步
- `Shutdown` → `break` — 退出循环，`cmd_rx` 被 drop，所有后续 `send()` 返回 `BusClosed`

---

## 单元测试

> 每个测试标注测试角度

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::{Message, NodeId};
    use std::time::Duration;

    // Helper to create a bus with default test parameters
    fn test_bus() -> Bus {
        Bus::new(
            Duration::from_secs(1),   // heartbeat_interval (unused in 1.2)
            Duration::from_secs(3),   // heartbeat_timeout (unused in 1.2)
            16,                        // channel_capacity
        )
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — construction & properties
    // ═══════════════════════════════════════════════════════════════

    // [构造] Bus::new() 不 panic，message_count 起始为 0
    #[tokio::test]
    async fn bus_new_creates_empty_bus() {
        let bus = test_bus();
        assert_eq!(bus.message_count(), 0);
        assert!(bus.uptime_ms() < 100); // just created
        bus.shutdown().await;
    }

    // [trait] Bus 是 Send + Sync（编译期验证）
    #[test]
    fn bus_is_send_and_sync() {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<Bus>();
    }

    // [数据] uptime_ms 随时间增长
    #[tokio::test]
    async fn bus_uptime_increases() {
        let bus = test_bus();
        let t0 = bus.uptime_ms();
        tokio::time::sleep(Duration::from_millis(10)).await;
        let t1 = bus.uptime_ms();
        assert!(t1 >= t0 + 10);
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — send & subscribe
    // ═══════════════════════════════════════════════════════════════

    // [数据] send 一条消息 → subscribe 的 receiver 收到同一条
    #[tokio::test]
    async fn send_and_receive_single_message() {
        let bus = test_bus();
        let mut rx = bus.subscribe();

        let msg = Message::new("test", NodeId::new("sender"), None, serde_json::json!("hello"));
        let msg_id = msg.id;
        bus.send(msg).await.unwrap();

        let received = rx.recv().await.unwrap();
        assert_eq!(received.id, msg_id);
        assert_eq!(received.msg_type, "test");
        assert_eq!(received.payload, serde_json::json!("hello"));

        bus.shutdown().await;
    }

    // [数据] 多条消息按发送顺序被 receiver 收到
    #[tokio::test]
    async fn messages_arrive_in_order() {
        let bus = test_bus();
        let mut rx = bus.subscribe();

        for i in 0..5 {
            let msg = Message::new("t", NodeId::new("s"), None, serde_json::json!(i));
            bus.send(msg).await.unwrap();
        }

        for i in 0..5 {
            let received = rx.recv().await.unwrap();
            assert_eq!(received.payload, serde_json::json!(i));
        }

        bus.shutdown().await;
    }

    // [数据] 多个 subscriber 都收到同一条广播消息
    #[tokio::test]
    async fn multiple_subscribers_all_receive() {
        let bus = test_bus();
        let mut rx1 = bus.subscribe();
        let mut rx2 = bus.subscribe();

        let msg = Message::new("t", NodeId::new("s"), None, serde_json::json!("x"));
        let msg_id = msg.id;
        bus.send(msg).await.unwrap();

        let r1 = rx1.recv().await.unwrap();
        let r2 = rx2.recv().await.unwrap();
        assert_eq!(r1.id, msg_id);
        assert_eq!(r2.id, msg_id);

        bus.shutdown().await;
    }

    // [数据] message_count 随每次 send 递增
    #[tokio::test]
    async fn message_count_increments() {
        let bus = test_bus();

        for i in 1..=3 {
            let msg = Message::new("t", NodeId::new("s"), None, serde_json::json!(null));
            bus.send(msg).await.unwrap();
            assert_eq!(bus.message_count(), i);
        }

        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — shutdown & error
    // ═══════════════════════════════════════════════════════════════

    // [关闭] shutdown 后 send 返回 BusClosed
    #[tokio::test]
    async fn send_after_shutdown_returns_bus_closed() {
        let bus = test_bus();
        bus.shutdown().await;

        let msg = Message::new("t", NodeId::new("s"), None, serde_json::json!(null));
        let result = bus.send(msg).await;
        assert!(matches!(result, Err(SendError::BusClosed)));
    }

    // [关闭] shutdown 不 panic（幂等性不重要，因为 shutdown consume self）
    #[tokio::test]
    async fn shutdown_stops_message_loop() {
        let bus = test_bus();
        // Verify shutdown completes without hanging
        tokio::time::timeout(Duration::from_secs(1), bus.shutdown())
            .await
            .expect("shutdown should complete quickly");
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — channel capacity
    // ═══════════════════════════════════════════════════════════════

    // [边界] channel_capacity=1 的 bus 仍可正常收发
    #[tokio::test]
    async fn bus_with_minimal_capacity() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            1, // minimal capacity
        );
        let mut rx = bus.subscribe();

        let msg = Message::new("t", NodeId::new("s"), None, serde_json::json!("x"));
        bus.send(msg).await.unwrap();
        let received = rx.recv().await.unwrap();
        assert_eq!(received.payload, serde_json::json!("x"));

        bus.shutdown().await;
    }

    // [边界] slow receiver 导致 lag（broadcast channel 的 lag 检测）
    #[tokio::test]
    async fn slow_receiver_gets_lagged_error() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            2, // tiny ring buffer
        );
        let mut rx = bus.subscribe();

        // Send 3 messages — ring buffer holds 2, so the first is overwritten
        for i in 0..3 {
            let msg = Message::new("t", NodeId::new("s"), None, serde_json::json!(i));
            bus.send(msg).await.unwrap();
        }

        // Now the receiver tries to catch up — the first receive should be Lagged
        let result = rx.recv().await;
        match result {
            Err(broadcast::error::RecvError::Lagged(n)) => {
                assert!(n >= 1, "should have lagged by at least 1 message");
            }
            other => panic!("expected Lagged error, got {other:?}"),
        }

        bus.shutdown().await;
    }

    // [构造] channel_capacity=0 不 panic（tokio broadcast 允许 capacity=0）
    #[tokio::test]
    async fn bus_with_zero_capacity() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            0,
        );
        // Even with 0 capacity, the bus itself should be alive
        assert_eq!(bus.message_count(), 0);
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — 定向消息在广播通道上的行为
    // ═══════════════════════════════════════════════════════════════

    // [数据] 定向消息（to=Some）同样广播，所有 subscriber 都能收到
    #[tokio::test]
    async fn directed_message_broadcast_to_all() {
        let bus = test_bus();
        let mut rx1 = bus.subscribe();
        let mut rx2 = bus.subscribe();

        let msg = Message::new(
            "tool_call",
            NodeId::new("engine"),
            Some(NodeId::new("mcp/filesystem")), // directed
            serde_json::json!("do_thing"),
        );
        bus.send(msg).await.unwrap();

        // Both subscribers see it — CAN model: all messages on the wire
        assert!(rx1.recv().await.is_ok());
        assert!(rx2.recv().await.is_ok());

        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — 并发
    // ═══════════════════════════════════════════════════════════════

    // [并发] 多个 task 同时 send 不丢消息
    #[tokio::test]
    async fn concurrent_sends_all_delivered() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            256,
        );
        let mut rx = bus.subscribe();

        let bus_ref = &bus;
        let handles: Vec<_> = (0..10)
            .map(|i| {
                tokio::spawn(async move {
                    let msg = Message::new(
                        "t",
                        NodeId::new("s"),
                        None,
                        serde_json::json!(i),
                    );
                    bus_ref.send(msg).await.unwrap();
                })
            })
            .collect();

        for h in handles {
            h.await.unwrap();
        }

        assert_eq!(bus.message_count(), 10);

        // All messages should be receivable (order not guaranteed for concurrent sends)
        let mut received = Vec::new();
        for _ in 0..10 {
            let r = rx.recv().await.unwrap();
            received.push(r.payload.as_u64().unwrap());
        }
        received.sort();
        assert_eq!(received, (0..10).collect::<Vec<_>>());

        bus.shutdown().await;
    }
}
```

---

## 测试清单

| # | 角度 | 测试名 | 覆盖 |
|---|------|--------|------|
| 1 | `[构造]` | `bus_new_creates_empty_bus` | Bus 创建后计数为 0 |
| 2 | `[trait]` | `bus_is_send_and_sync` | 编译期 Send+Sync |
| 3 | `[数据]` | `bus_uptime_increases` | 运行时间单调增长 |
| 4 | `[数据]` | `send_and_receive_single_message` | 基本收发路径 |
| 5 | `[数据]` | `messages_arrive_in_order` | 消息顺序保证 |
| 6 | `[数据]` | `multiple_subscribers_all_receive` | 广播：多订阅者同收 |
| 7 | `[数据]` | `message_count_increments` | 计数器递增 |
| 8 | `[关闭]` | `send_after_shutdown_returns_bus_closed` | shutdown 后 send 报错 |
| 9 | `[关闭]` | `shutdown_stops_message_loop` | shutdown 不挂起 |
| 10 | `[边界]` | `bus_with_minimal_capacity` | capacity=1 正常工作 |
| 11 | `[边界]` | `slow_receiver_gets_lagged_error` | Lagged 检测 |
| 12 | `[构造]` | `bus_with_zero_capacity` | capacity=0 不 panic |
| 13 | `[数据]` | `directed_message_broadcast_to_all` | CAN 模型：定向也广播 |
| 14 | `[并发]` | `concurrent_sends_all_delivered` | 10 并发 send 不丢消息 |

---

## 小结

- **两条 channel**：`mpsc` 接收命令 + `broadcast` 广播消息
- **消息循环**：收 cmd → 广播 → 计数，运行在独立 tokio task 中
- **Dummy receiver**：Bus 自己持有一个 receiver 保持 broadcast channel 存活（CAN 永不断电）
- **shutdown**：发 Shutdown 命令 → 消息循环退出 → Bus 被 consume
- **14 个测试**，覆盖构造、基本路径、边界（capacity=0/1）、shutdown、慢消费者 lag、并发
