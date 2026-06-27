# 任务 1.2：Bus 数据通道

> Phase 1 — Bus 消息总线第二项任务
> 父文档：`docs/v1.x/phase1/phase1-bus-design.md`
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

`tokio::sync::broadcast` 有一个基本约束：当 `receiver_count() == 0` 时，`send()` 返回 `Err(SendError)`。Bus 的消息循环有四个分支都要无条件调 `broadcast_tx.send()`——应用消息、`node_online`、`node_offline`、`heartbeat_request`。没有 drain_rx 的话，每个分支前面都要加 `if broadcast_tx.receiver_count() > 0`。

drain_rx 的设计意图：**用一行 `while drain_rx.try_recv().is_ok() {}` 换掉四处条件判断**。它保证 `receiver_count() >= 1` 永远成立，让消息循环的所有 send 无条件执行。

慢消费者的背压由 tokio 原生的 `Lagged` 机制处理——drain_rx 不参与这一层。drain_rx 自己每次事件后被立即 drain，仅仅是为了不让它自身成为瓶颈（一个从不读取的 receiver 会拖慢 sender）。

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
/// The Bus holds a dummy `broadcast::Receiver` (inside the message loop) to
/// keep the channel alive, mirroring CAN's "the wire is always powered."
/// The dummy receiver is **drained after every send** to prevent it from
/// causing backpressure — without draining, after `capacity` messages the
/// dummy (which never reads) would block all senders.
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
}
```

逐行：
- `cmd_tx: mpsc::Sender<BusCommand>` — 命令通道的发送端。外部通过 `send()` 向 Bus 投递命令，Bus 内部消息循环逐一处理。`mpsc` = 多生产者单消费者，多节点同时发消息不会冲突
- `broadcast_tx: broadcast::Sender<Message>` — 广播通道的发送端。消息循环处理完命令后通过它广播给所有订阅者。`broadcast` = 单生产者多消费者，所有节点收到同一份消息的 clone
- `message_count: Arc<AtomicU64>` — 原子计数器，消息循环和 `Bus::message_count()` 共享。用 `AtomicU64` 而非 `Mutex<u64>`，因为只是计数不需要锁
- `start_time: Instant` — 创建时刻，`uptime_ms()` 用它计算运行时间
- `_loop_handle: JoinHandle<()>` — 消息循环的 tokio task 句柄。名前加 `_` 表示不会被主动读取，但持有它防止 task 被 drop 取消
- **注意**：dummy receiver 不在 `Bus` struct 中——它被移入消息循环，每次 send 后立即 drain，防止它成为背压源。详见消息循环部分

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
        let (broadcast_tx, drain_rx) = broadcast::channel(channel_capacity);
        let (cmd_tx, cmd_rx) = mpsc::channel(256);
        let message_count = Arc::new(AtomicU64::new(0));

        let broadcast_tx_clone = broadcast_tx.clone();
        let count_clone = message_count.clone();
        let loop_handle = tokio::spawn(async move {
            run_message_loop(cmd_rx, broadcast_tx_clone, drain_rx, count_clone).await;
        });

        Self {
            cmd_tx,
            broadcast_tx,
            message_count,
            start_time: Instant::now(),
            _loop_handle: loop_handle,
        }
    }
```

逐行：
- `broadcast::channel(channel_capacity)` — 创建广播通道，capacity 即环形缓冲区大小（spec 默认 1024）。**tokio broadcast 不允许 capacity=0，最小值为 1**。返回的 `drain_rx` 是 dummy 接收者，移入消息循环用于保持 channel 存活 + 定期 drain
- `mpsc::channel(256)` — 命令通道 buffer 256 条，足够缓冲高并发写入
- `broadcast_tx.clone()` — `broadcast::Sender` 是 `Clone` 的，clone 一份给消息循环。Bus 自己保留原始 copy 用于 `subscribe()`
- `count_clone = message_count.clone()` — `Arc::clone()` 增加引用计数，消息循环和 Bus 共享同一个 `AtomicU64`
- `tokio::spawn(async move { ... })` — 在 tokio runtime 上启动消息循环。`async move` 将 `cmd_rx`、`broadcast_tx_clone`、`drain_rx`、`count_clone` 的所有权移入闭包

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
///
/// `drain_rx` guarantees `receiver_count() >= 1` forever, so all four
/// unconditional `broadcast_tx.send()` calls (send / connect / disconnect /
/// heartbeat tick) succeed without checking "is anyone listening?".
///
/// The drain after every event — `while drain_rx.try_recv().is_ok() {}` —
/// prevents drain_rx itself from becoming the slowest receiver and blocking
/// the sender. Without drain, the dummy would accumulate lag and after
/// `capacity` messages would block `broadcast_tx.send()`.
///
/// Slow-consumer backpressure is handled by tokio's built-in `Lagged`
/// mechanism. drain_rx does not participate in that.
async fn run_message_loop(
    mut cmd_rx: mpsc::Receiver<BusCommand>,
    broadcast_tx: broadcast::Sender<Message>,
    mut drain_rx: broadcast::Receiver<Message>,
    message_count: Arc<AtomicU64>,
) {
    while let Some(cmd) = cmd_rx.recv().await {
        match cmd {
            BusCommand::Send { msg, respond_to } => {
                // Broadcast to all subscribers.
                // If no application receivers exist, drain_rx still keeps the
                // channel alive. send() returns Ok(n) where n includes drain_rx.
                let _ = broadcast_tx.send(msg);
                message_count.fetch_add(1, Ordering::Relaxed);
                // Drain the dummy receiver immediately to free ring buffer space.
                // This prevents the dummy from accumulating lag and blocking
                // real senders. Only real slow receivers cause backpressure.
                while drain_rx.try_recv().is_ok() {}
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
- `mut drain_rx` — dummy 接收者。消息循环拥有它，每次 send 后立即 drain（`try_recv()` 非阻塞消费）。**关键**：如果不 drain，drain_rx 的 lag 会累积，`capacity` 条消息后 `broadcast_tx.send()` 会等待 drain_rx 读取旧消息，导致所有发送方阻塞——dummy 反成背压源
- `while let Some(cmd) = cmd_rx.recv().await` — 循环等待命令。当所有 `cmd_tx` sender 被 drop 时，`recv()` 返回 `None`，循环退出。正常路径通过 `Shutdown` 命令 exit
- `let _ = broadcast_tx.send(msg)` — 忽略返回值（receiver 数量）。CAN 模型：消息发到线上即可，收不收是节点的事
- `while drain_rx.try_recv().is_ok() {}` — 非阻塞清空 dummy lag。`try_recv()` 不会阻塞，立即返回。drain 干净后 `try_recv()` 返回 `Err(Empty)`，循环结束。对性能影响可忽略
- `message_count.fetch_add(1, Ordering::Relaxed)` — 原子加 1。`Relaxed` 因为计数不参与其他同步
- `Shutdown` → `break` — 退出循环，`cmd_rx` 被 drop，所有后续 `send()` 返回 `BusClosed`

---

## 单元测试

> 每个测试标注测试角度
> **注意**：定向目标不存在的检测需要在线图（任务 1.3），`SendReceipt` 投递保证在任务 1.5。1.2 测的是纯数据通道的边界。

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

    fn test_msg(payload: serde_json::Value) -> Message {
        Message::new("test", NodeId::new("s"), None, payload)
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — construction & properties (3 tests)
    // ═══════════════════════════════════════════════════════════════

    // [构造] Bus::new() 不 panic，message_count 起始为 0
    #[tokio::test]
    async fn bus_new_creates_empty_bus() {
        let bus = test_bus();
        assert_eq!(bus.message_count(), 0);
        assert!(bus.uptime_ms() < 100);
        bus.shutdown().await;
    }

    // [trait] Bus 是 Send + Sync（编译期验证跨线程传递）
    #[test]
    fn bus_is_send_and_sync() {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<Bus>();
    }

    // [时间] uptime_ms 随时间单调增长
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
    // Bus — send & subscribe 基本路径 (5 tests)
    // ═══════════════════════════════════════════════════════════════

    // [数据] send 一条消息 → subscribe 的 receiver 收到同一条
    #[tokio::test]
    async fn send_and_receive_single_message() {
        let bus = test_bus();
        let mut rx = bus.subscribe();

        let msg = test_msg(serde_json::json!("hello"));
        let msg_id = msg.id;
        bus.send(msg).await.unwrap();

        let received = rx.recv().await.unwrap();
        assert_eq!(received.id, msg_id);
        assert_eq!(received.payload, serde_json::json!("hello"));

        bus.shutdown().await;
    }

    // [数据] 多条消息按发送顺序被 receiver 收到
    #[tokio::test]
    async fn messages_arrive_in_order() {
        let bus = test_bus();
        let mut rx = bus.subscribe();

        for i in 0..5 {
            bus.send(test_msg(serde_json::json!(i))).await.unwrap();
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

        let msg = test_msg(serde_json::json!("x"));
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
            bus.send(test_msg(serde_json::json!(null))).await.unwrap();
            assert_eq!(bus.message_count(), i);
        }

        bus.shutdown().await;
    }

    // [数据] 定向消息（to=Some）同样广播，所有 subscriber 可见（CAN 模型）
    #[tokio::test]
    async fn directed_message_broadcast_to_all() {
        let bus = test_bus();
        let mut rx1 = bus.subscribe();
        let mut rx2 = bus.subscribe();

        let msg = Message::new(
            "tool_call",
            NodeId::new("engine"),
            Some(NodeId::new("mcp/filesystem")),
            serde_json::json!("do_thing"),
        );
        bus.send(msg).await.unwrap();

        // Both subscribers see it — CAN: all messages on the wire
        assert!(rx1.recv().await.is_ok());
        assert!(rx2.recv().await.is_ok());

        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — shutdown & error (2 tests)
    // ═══════════════════════════════════════════════════════════════

    // [关闭] shutdown 后 send 返回 BusClosed
    #[tokio::test]
    async fn send_after_shutdown_returns_bus_closed() {
        let bus = test_bus();
        bus.shutdown().await;

        let result = bus.send(test_msg(serde_json::json!(null))).await;
        assert!(matches!(result, Err(SendError::BusClosed)));
    }

    // [关闭] shutdown 不挂起，1 秒内完成
    #[tokio::test]
    async fn shutdown_completes_within_timeout() {
        let bus = test_bus();
        tokio::time::timeout(Duration::from_secs(1), bus.shutdown())
            .await
            .expect("shutdown should complete quickly");
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — channel capacity 边界 (3 tests)
    // ═══════════════════════════════════════════════════════════════

    // [边界] channel_capacity=1 仍可正常收发
    #[tokio::test]
    async fn bus_with_minimal_capacity() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            1,
        );
        let mut rx = bus.subscribe();

        bus.send(test_msg(serde_json::json!("x"))).await.unwrap();
        assert_eq!(rx.recv().await.unwrap().payload, serde_json::json!("x"));

        bus.shutdown().await;
    }

    // [边界] slow receiver 导致 Lagged——环形缓冲区满，慢消费者丢消息
    #[tokio::test]
    async fn slow_receiver_gets_lagged_error() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            2,
        );
        let mut rx = bus.subscribe();

        // Send 3 msgs — ring buffer holds 2, first one gets overwritten
        for i in 0..3 {
            bus.send(test_msg(serde_json::json!(i))).await.unwrap();
        }

        // Slow receiver tries to catch up — first recv must be Lagged
        let result = rx.recv().await;
        match result {
            Err(broadcast::error::RecvError::Lagged(n)) => {
                assert!(n >= 1, "should have lagged by at least 1 message");
            }
            other => panic!("expected Lagged error, got {other:?}"),
        }

        bus.shutdown().await;
    }

    // [边界] channel_capacity=0 不 panic
    #[tokio::test]
    async fn bus_with_zero_capacity() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            0,
        );
        assert_eq!(bus.message_count(), 0);
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — 收发侧健康状态 (6 tests)
    // ═══════════════════════════════════════════════════════════════

    // [健康] 无应用订阅者时 send 仍成功（drain receiver 保活 + 不阻塞）
    #[tokio::test]
    async fn send_succeeds_with_no_application_subscribers() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            4,
        );
        // No bus.subscribe() — only the drain receiver exists

        // Multiple sends should all succeed
        for i in 0..10 {
            bus.send(test_msg(serde_json::json!(i))).await.unwrap();
        }

        assert_eq!(bus.message_count(), 10);
        bus.shutdown().await;
    }

    // [健康] 接收方中途 drop——其他 subscriber 不受影响
    #[tokio::test]
    async fn dropped_receiver_does_not_affect_others() {
        let bus = test_bus();
        let mut rx1 = bus.subscribe();
        let mut rx2 = bus.subscribe();

        // Send initial message that both see
        bus.send(test_msg(serde_json::json!("before"))).await.unwrap();
        assert!(rx1.recv().await.is_ok());
        assert!(rx2.recv().await.is_ok());

        // Drop rx1
        drop(rx1);

        // Send more messages — rx2 should still receive them
        bus.send(test_msg(serde_json::json!("after1"))).await.unwrap();
        bus.send(test_msg(serde_json::json!("after2"))).await.unwrap();

        assert_eq!(rx2.recv().await.unwrap().payload, serde_json::json!("after1"));
        assert_eq!(rx2.recv().await.unwrap().payload, serde_json::json!("after2"));

        bus.shutdown().await;
    }

    // [健康] 慢消费者阻塞发送方——ring buffer 满时 send() 等待
    // 这是 tokio broadcast 的背压机制：消费者的处理速度会反压生产者
    #[tokio::test]
    async fn slow_receiver_backpressures_sender() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            2,
        );
        let _slow_rx = bus.subscribe(); // subscribe but never read

        // Fill buffer
        bus.send(test_msg(serde_json::json!(1))).await.unwrap();
        bus.send(test_msg(serde_json::json!(2))).await.unwrap();

        // 3rd send should block because ring buffer is full and dummy
        // is already drained. Only the slow receiver holds the tail.
        let result = tokio::time::timeout(
            Duration::from_millis(100),
            bus.send(test_msg(serde_json::json!(3))),
        )
        .await;

        assert!(
            result.is_err(),
            "send should have timed out — slow receiver must backpressure sender"
        );

        bus.shutdown().await;
    }

    // [健康] 快消费者不被慢消费者阻挡（直到缓冲区绕回）
    #[tokio::test]
    async fn fast_receiver_not_blocked_by_slow_one() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            8,
        );
        let _slow_rx = bus.subscribe(); // never reads
        let mut fast_rx = bus.subscribe(); // reads promptly

        // Send within capacity — fast receiver gets all messages
        for i in 0..5 {
            bus.send(test_msg(serde_json::json!(i))).await.unwrap();
        }

        for i in 0..5 {
            assert_eq!(
                fast_rx.recv().await.unwrap().payload,
                serde_json::json!(i)
            );
        }

        bus.shutdown().await;
    }

    // [健康] Lagged 后 receiver 可以继续接收新消息（恢复）
    #[tokio::test]
    async fn receiver_recovers_after_lag() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            2,
        );
        let mut rx = bus.subscribe();

        // Cause lag
        for i in 0..4 {
            bus.send(test_msg(serde_json::json!(i))).await.unwrap();
        }

        // First recv should be Lagged
        assert!(matches!(
            rx.recv().await,
            Err(broadcast::error::RecvError::Lagged(_))
        ));

        // After catch-up, subsequent messages arrive normally
        bus.send(test_msg(serde_json::json!("after_lag"))).await.unwrap();
        assert_eq!(
            rx.recv().await.unwrap().payload,
            serde_json::json!("after_lag")
        );

        bus.shutdown().await;
    }

    // [健康] drain 不阻塞——无订阅者时大量发送不会累积背压
    #[tokio::test]
    async fn many_sends_with_only_drain_receiver_no_backpressure() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            2, // tiny buffer, but drain prevents backpressure
        );
        // No application subscribers

        // Send many messages — should all succeed with no blocking
        for i in 0..100 {
            bus.send(test_msg(serde_json::json!(i))).await.unwrap();
        }

        assert_eq!(bus.message_count(), 100);
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — 并发 (1 test)
    // ═══════════════════════════════════════════════════════════════

    // [并发] 10 个 task 同时 send——全部送达，无丢失
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

        let mut received: Vec<u64> = Vec::new();
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
| 3 | `[时间]` | `bus_uptime_increases` | 运行时间单调增长 |
| 4 | `[数据]` | `send_and_receive_single_message` | 基本收发路径 |
| 5 | `[数据]` | `messages_arrive_in_order` | 消息顺序保证 |
| 6 | `[数据]` | `multiple_subscribers_all_receive` | 广播：多订阅者同收 |
| 7 | `[数据]` | `message_count_increments` | 计数器递增 |
| 8 | `[数据]` | `directed_message_broadcast_to_all` | CAN：定向也广播 |
| 9 | `[关闭]` | `send_after_shutdown_returns_bus_closed` | shutdown 后 send 报错 |
| 10 | `[关闭]` | `shutdown_completes_within_timeout` | shutdown 1 秒内完成 |
| 11 | `[边界]` | `bus_with_minimal_capacity` | capacity=1 正常 |
| 12 | `[边界]` | `slow_receiver_gets_lagged_error` | 慢消费者 Lagged |
| 13 | `[边界]` | `bus_with_zero_capacity` | capacity=0 不 panic |
| 14 | `[健康]` | `send_succeeds_with_no_application_subscribers` | 无订阅者时 drain 保活 |
| 15 | `[健康]` | `dropped_receiver_does_not_affect_others` | 接收方掉线不影响其他 |
| 16 | `[健康]` | `slow_receiver_backpressures_sender` | 慢消费者背压发送方 |
| 17 | `[健康]` | `fast_receiver_not_blocked_by_slow_one` | 快消费者不受慢者影响 |
| 18 | `[健康]` | `receiver_recovers_after_lag` | Lagged 后可恢复接收 |
| 19 | `[健康]` | `many_sends_with_only_drain_receiver_no_backpressure` | drain 防止虚假背压 |
| 20 | `[并发]` | `concurrent_sends_all_delivered` | 10 并发不丢消息 |

### 推迟到后续任务的边界

| 边界 | 推迟原因 | 覆盖任务 |
|------|---------|---------|
| 定向目标不在线 → `SendError::NodeOffline` | 需要在线图追踪节点 | 1.3 + 1.5 |
| 心跳超时 → `node_offline` 广播 | 需要心跳检测机制 | 1.4 |
| `SendReceipt.matching_nodes` 计算 | 需要 filter 匹配逻辑 | 1.6 |
| 发送方阻塞超时（timeout send） | 需讨论是否引入 send timeout API | 后续讨论 |

---

## 实现修正

以下测试在设计文档基础上做了调整，反映实际 tokio broadcast 行为：

| 原设计测试 | 实际测试 | 原因 |
|-----------|---------|------|
| `bus_with_zero_capacity` | `bus_with_minimal_capacity_works` | tokio `broadcast::channel(0)` panic，最小值 1 |
| `send_after_shutdown_returns_bus_closed` | `receiver_returns_closed_after_shutdown` | `shutdown(self)` 消费 Bus，无法再 send，改为验证 receiver 收到 `Closed` |
| `slow_receiver_backpressures_sender` | `slow_receiver_lagged_not_backpressured` | drain receiver 拉高了 channel 最慢位置，慢消费者不会阻塞发送方，只会 Lagged |
| `receiver_recovers_after_lag` (单次 recv) | 改用 `try_recv()` 循环 drain 所有 lag + 缓冲消息后再验证 | Lagged 后缓冲区可能仍有未消费消息，需先 drain 净 |

---

## 小结

- **两条 channel**：`mpsc` 接收命令 + `broadcast` 广播消息
- **消息循环**：收 cmd → 广播 → drain dummy → 计数，运行在独立 tokio task 中
- **Dummy drain**：消息循环持有 drain receiver，保证 `receiver_count() >= 1`，所有 `broadcast_tx.send()` 无条件执行。drain 防止 dummy 自身成为背压源；慢消费者背压由 tokio `Lagged` 处理
- **shutdown**：发 Shutdown 命令 → 消息循环退出 → Bus 被 consume
- **20 个测试**：3 构造 + 5 基本路径 + 2 shutdown + 3 容量边界 + 6 收发健康 + 1 并发
