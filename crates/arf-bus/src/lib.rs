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
        let _ = (heartbeat_interval, heartbeat_timeout); // used in task 1.4

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

    /// Send a message to be broadcast on the bus.
    ///
    /// Returns `Ok(())` if the message was successfully broadcast.
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
    /// Drops the message loop task and the drain receiver,
    /// causing all subsequent `send()` calls to return `BusClosed`.
    pub async fn shutdown(self) {
        let (tx, rx) = oneshot::channel();
        let _ = self.cmd_tx.send(BusCommand::Shutdown { respond_to: tx }).await;
        let _ = rx.await;
    }
}

// ═══════════════════════════════════════════════════════════════════
// Message loop
// ═══════════════════════════════════════════════════════════════════

/// Internal message loop — the heart of the Bus.
///
/// Receives commands from `cmd_rx`, processes them, and broadcasts messages
/// through `broadcast_tx`. Runs in a dedicated tokio task.
///
/// `drain_rx` is the dummy receiver that keeps the broadcast channel alive
/// (CAN's "wire is always powered"). It is **drained after every send** to
/// prevent it from becoming a backpressure bottleneck — without draining,
/// after `capacity` messages the dummy (which never reads via `recv()`)
/// would block `broadcast_tx.send()`, blocking all senders.
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

// ═══════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::{Message, NodeId};
    use std::time::Duration;

    // Helper to create a bus with default test parameters
    fn test_bus() -> Bus {
        Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            16,
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

    // [关闭] shutdown 后 subscriber 收到 Closed——shutdown 传播到接收方
    #[tokio::test]
    async fn receiver_returns_closed_after_shutdown() {
        let bus = test_bus();
        let mut rx = bus.subscribe();

        // Send a message to confirm it works
        bus.send(test_msg(serde_json::json!("pre_shutdown"))).await.unwrap();
        assert!(rx.recv().await.is_ok());

        // Shutdown — consumes bus, broadcast channel closes
        bus.shutdown().await;

        // Receiver detects channel closed
        let result = rx.recv().await;
        assert!(
            matches!(result, Err(broadcast::error::RecvError::Closed)),
            "receiver should get Closed after shutdown, got {result:?}"
        );
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

    // [边界] channel_capacity=1 仍可正常收发（最小合法值）
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

    // [边界] channel_capacity=1 是最小合法值；tokio broadcast channel 不允许 capacity=0
    #[tokio::test]
    async fn bus_with_minimal_capacity_works() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            1,
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

    // [健康] 慢消费者不阻塞发送方——drain 保活，慢消费者自行 Lagged
    // tokio broadcast 的 drain receiver 拉高了 channel 的最慢位置，
    // 慢消费者不会造成背压——它只会收到 Lagged 通知。
    #[tokio::test]
    async fn slow_receiver_lagged_not_backpressured() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            2,
        );
        let _slow_rx = bus.subscribe(); // subscribe but never read

        // Send many messages — should all succeed (no blocking)
        // because drain keeps the ring buffer tail moving
        for i in 0..10 {
            bus.send(test_msg(serde_json::json!(i))).await.unwrap();
        }
        assert_eq!(bus.message_count(), 10);
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

    // [健康] Lagged 后 receiver 可恢复接收新消息——跳过 lag，继续消费
    #[tokio::test]
    async fn receiver_recovers_after_lag() {
        let bus = Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            2,
        );
        let mut rx = bus.subscribe();

        // Cause lag by sending without reading
        for i in 0..4 {
            bus.send(test_msg(serde_json::json!(i))).await.unwrap();
        }

        // Drain everything: catch Lagged + any buffered messages
        let mut lag_count = 0u64;
        let mut _drained = 0usize;
        loop {
            // Use try_recv to avoid blocking — we may have caught up
            match rx.try_recv() {
                Err(broadcast::error::TryRecvError::Lagged(n)) => {
                    lag_count += n;
                }
                Err(broadcast::error::TryRecvError::Empty) => {
                    break; // caught up
                }
                Err(broadcast::error::TryRecvError::Closed) => {
                    panic!("unexpected closed");
                }
                Ok(_) => {
                    _drained += 1;
                }
            }
        }
        assert!(lag_count > 0, "should have lagged by at least 1 message");

        // After catch-up, new messages arrive normally
        bus.send(test_msg(serde_json::json!("after_lag"))).await.unwrap();
        // recv should return the newly sent message (not a leftover)
        let recovered = rx.recv().await.unwrap();
        assert_eq!(recovered.payload, serde_json::json!("after_lag"));

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
        let bus = std::sync::Arc::new(Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            256,
        ));
        let mut rx = bus.subscribe();

        let handles: Vec<_> = (0..10)
            .map(|i| {
                let bus = bus.clone();
                tokio::spawn(async move {
                    let msg = Message::new(
                        "t",
                        NodeId::new("s"),
                        None,
                        serde_json::json!(i),
                    );
                    bus.send(msg).await.unwrap();
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

        // All spawned tasks completed — only our Arc reference remains
        let bus = std::sync::Arc::into_inner(bus)
            .expect("all other Arc references should be dropped");
        bus.shutdown().await;
    }
}
