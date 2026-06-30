//! ARF Bus — J-RPC broadcast message bus.
//!
//! All nodes communicate through this bus. It maintains an online node graph,
//! handles node lifecycle (online/offline/heartbeat), and routes messages
//! (broadcast when `to` is empty, directed otherwise).

use arf_core::{BusId, Message, MessageFilter, NodeId, NodeInfo, SendError, SendReceipt};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};
use tokio::sync::{broadcast, mpsc, oneshot};
use uuid::Uuid;

// ═══════════════════════════════════════════════════════════════════
// ConnectError
// ═══════════════════════════════════════════════════════════════════

/// Errors that can occur when connecting to the Bus.
#[derive(Debug)]
pub enum ConnectError {
    /// A node with this NodeId is already connected.
    AlreadyConnected(NodeId),
    /// Bus has been shut down.
    BusClosed,
}

impl std::fmt::Display for ConnectError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::AlreadyConnected(id) => write!(f, "node already connected: {id}"),
            Self::BusClosed => write!(f, "bus closed"),
        }
    }
}

impl std::error::Error for ConnectError {}

// ═══════════════════════════════════════════════════════════════════
// NodeEntry
// ═══════════════════════════════════════════════════════════════════

/// Internal per-node state stored in the Bus nodes map.
pub(crate) struct NodeEntry {
    pub info: NodeInfo,
    /// Last heartbeat ack received. Used in task 1.4.
    pub last_ack: Instant,
    pub filter: MessageFilter,
}

// ═══════════════════════════════════════════════════════════════════
// Bus
// ═══════════════════════════════════════════════════════════════════

/// J-RPC broadcast message bus — CAN model: single wire, all nodes on it.
///
/// Two channels power the Bus:
/// - `cmd_tx` (mpsc): nodes send commands to the bus (send, connect, heartbeat ack)
/// - `broadcast_tx` (broadcast): bus broadcasts messages to all subscribers
///
/// # drain_rx — eliminating the SendError path
///
/// `tokio::sync::broadcast::send()` returns `Err(SendError)` when
/// `receiver_count() == 0`. The message loop has four branches that all call
/// `broadcast_tx.send()` unconditionally:
///
/// - `BusCommand::Send` → `broadcast_tx.send(msg)`
/// - `BusCommand::Connect` → `broadcast_tx.send(node_online)`
/// - `BusCommand::Disconnect` → `broadcast_tx.send(node_offline)`
/// - heartbeat tick → `broadcast_tx.send(heartbeat_request)`
///
/// Without drain_rx, each of these would need a `if receiver_count() > 0` guard.
/// drain_rx is the single `broadcast::Receiver` returned by `broadcast::channel()`,
/// held permanently inside the message loop. It guarantees `receiver_count() >= 1`
/// forever, so all four send points remain unconditional.
///
/// The drain — `while drain_rx.try_recv().is_ok() {}` after every event — is
/// one line of code. It replaces four conditional checks and a runtime invariant
/// ("is anyone listening?"). Slow-consumer backpressure is handled by tokio's
/// built-in `Lagged` mechanism; drain_rx does not participate in that.
///
/// # Design tradeoff: racing mode
///
/// The Bus guarantees `send()` never blocks. Consumers that keep up stay live;
/// slow consumers get `Lagged(n)` alone and never backpressure the sender or
/// other fast consumers. If you need per-message delivery guarantees, build
/// them at the application layer (persistence + retry) — the Bus layer handles
/// transport only.
pub struct Bus {
    /// Stable Bus identifier (Phase 6 multi-Bus). Generated at construction.
    pub id: BusId,
    /// Send commands into the bus message loop.
    pub(crate) cmd_tx: mpsc::Sender<BusCommand>,
    /// Clone of the broadcast sender, used by `subscribe()`.
    /// Wrapped in Option so `signal_shutdown` can `take()` and drop it,
    /// closing the broadcast channel and unblocking all receivers.
    pub(crate) broadcast_tx: Mutex<Option<broadcast::Sender<Message>>>,
    /// Online nodes registry — shared with message loop.
    pub(crate) nodes: Arc<RwLock<HashMap<NodeId, NodeEntry>>>,
    /// Total messages broadcast since start.
    message_count: Arc<AtomicU64>,
    /// When the bus was created.
    start_time: Instant,
    /// JoinHandle for the message loop task.
    _loop_handle: tokio::task::JoinHandle<()>,
}

/// Internal commands sent to the bus message loop.
pub(crate) enum BusCommand {
    /// Send a message to be broadcast.
    Send {
        msg: Message,
        respond_to: oneshot::Sender<Result<SendReceipt, SendError>>,
    },
    /// Connect a node to the bus.
    Connect {
        info: NodeInfo,
        filter: MessageFilter,
        respond_to: oneshot::Sender<Result<(), ConnectError>>,
    },
    /// Disconnect a node from the bus.
    Disconnect {
        node_id: NodeId,
        respond_to: oneshot::Sender<()>,
    },
    /// Heartbeat acknowledgement from a node.
    /// Sent automatically by NodeHandle when it intercepts a heartbeat_request.
    HeartbeatAck { node_id: NodeId },
    /// Shut down the bus.
    Shutdown { respond_to: oneshot::Sender<()> },
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
        let (broadcast_tx, drain_rx) = broadcast::channel(channel_capacity);
        let (cmd_tx, cmd_rx) = mpsc::channel(256);
        let message_count = Arc::new(AtomicU64::new(0));
        let nodes = Arc::new(RwLock::new(HashMap::new()));

        let broadcast_tx_clone = broadcast_tx.clone();
        let count_clone = message_count.clone();
        let nodes_clone = nodes.clone();
        let bus_id = BusId(Uuid::new_v4());
        let loop_handle = tokio::spawn({
            let bus_id = bus_id;
            async move {
                run_message_loop(
                    cmd_rx,
                    broadcast_tx_clone,
                    drain_rx,
                    count_clone,
                    nodes_clone,
                    heartbeat_interval,
                    heartbeat_timeout,
                    bus_id,
                )
                .await;
            }
        });

        Self {
            id: bus_id,
            cmd_tx,
            broadcast_tx: Mutex::new(Some(broadcast_tx)),
            nodes,
            message_count,
            start_time: Instant::now(),
            _loop_handle: loop_handle,
        }
    }

    /// Send a message to be broadcast on the bus.
    ///
    /// Returns `Ok(SendReceipt)` with online node counts.
    /// Returns `Err(SendError::BusClosed)` if the bus has been shut down.
    /// The message is stamped with this Bus's `id` as `from_bus` before
    /// broadcast (Phase 6 task 6.0.3).
    pub async fn send(&self, mut msg: Message) -> Result<SendReceipt, SendError> {
        msg.from_bus = Some(self.id);
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
        self.subscribe_internal()
    }

    /// Internal subscribe (used by `connect()` and `attach_to()` to wire
    /// forwarding tasks). Same as `subscribe()` but `pub(crate)` so it can
    /// be called from `connection.rs`.
    pub(crate) fn subscribe_internal(&self) -> broadcast::Receiver<Message> {
        self.broadcast_tx
            .lock()
            .unwrap()
            .as_ref()
            .expect("broadcast_tx should exist until shutdown")
            .subscribe()
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
        let _ = self
            .cmd_tx
            .send(BusCommand::Shutdown { respond_to: tx })
            .await;
        let _ = rx.await;
    }

    /// Send shutdown signal via try_send — usable from &self.
    ///
    /// Python bindings use this because Bus is Arc-wrapped and
    /// `shutdown(self)` cannot be called on Arc<Bus>.
    ///
    /// Also drops the broadcast sender to close the broadcast channel,
    /// so that all `NodeHandle::recv()` calls unblock with `Closed`.
    pub fn signal_shutdown(&self) {
        let (tx, _rx) = oneshot::channel();
        let _ = self
            .cmd_tx
            .try_send(BusCommand::Shutdown { respond_to: tx });
        // Drop the broadcast sender to close the channel.
        // After this, all broadcast::Receiver instances will get Closed.
        self.broadcast_tx.lock().unwrap().take();
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
/// Uses `tokio::select!` to multiplex between command processing and
/// heartbeat timer ticks. The heartbeat timer periodically broadcasts
/// `heartbeat_request` and checks for timed-out nodes.
///
/// # drain_rx — eliminating the SendError path
///
/// `drain_rx` is the `Receiver` returned by `broadcast::channel()`, held
/// permanently inside the message loop. Its sole purpose: guarantee
/// `receiver_count() >= 1` so that all four unconditional `broadcast_tx.send()`
/// calls (send / connect / disconnect / heartbeat tick) succeed without
/// checking "is anyone listening?".
///
/// The drain after every branch — `while drain_rx.try_recv().is_ok() {}` —
/// advances drain_rx to the sender's position, preventing it from becoming
/// the oldest non-lagged receiver. Without drain, drain_rx would lag and
/// eventually block `broadcast_tx.send()` as the slowest receiver. This is
/// not about lifecycle message semantics — it's purely about keeping the
/// dummy from being the bottleneck it was meant to prevent.
///
/// Slow-consumer backpressure is handled by tokio's built-in `Lagged`
/// mechanism. drain_rx does not participate in that.
async fn run_message_loop(
    mut cmd_rx: mpsc::Receiver<BusCommand>,
    broadcast_tx: broadcast::Sender<Message>,
    mut drain_rx: broadcast::Receiver<Message>,
    message_count: Arc<AtomicU64>,
    nodes: Arc<RwLock<HashMap<NodeId, NodeEntry>>>,
    heartbeat_interval: Duration,
    heartbeat_timeout: Duration,
    bus_id: BusId,
) {
    let mut heartbeat_timer = tokio::time::interval(heartbeat_interval);
    // Skip immediate first tick — give nodes time to connect
    heartbeat_timer.tick().await;

    loop {
        tokio::select! {
            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(BusCommand::Send { msg, respond_to }) => {
                        // Validate targets for directed messages
                        let mut online_targets = 0usize;
                        if !msg.to.is_empty() {
                            let map = nodes.read().unwrap();
                            let offline: Vec<NodeId> = msg.to.iter()
                                .filter(|t| !map.contains_key(t))
                                .cloned()
                                .collect();
                            if offline.len() == msg.to.len() {
                                let _ = respond_to
                                    .send(Err(SendError::NodeOffline(offline)));
                                continue;
                            }
                            online_targets = msg.to.len() - offline.len();
                        }

                        let msg_id = msg.id;
                        let is_broadcast = msg.to.is_empty();

                        // Count matching nodes using each entry's filter
                        let matching_nodes = if is_broadcast {
                            let map = nodes.read().unwrap();
                            map.values()
                                .filter(|entry| entry.filter.matches(&msg, &entry.info.node_id))
                                .count()
                        } else {
                            online_targets
                        };

                        let online_nodes = nodes.read().unwrap().len();
                        // CAN-bus Lagged semantics: sender never blocks. If all receivers
                        // have lagged past the ring buffer, tokio drops the message and
                        // returns an error here — we discard it. Slow consumers get
                        // Lagged(n) instead of backpressuring the sender.
                        let _ = broadcast_tx.send(msg);
                        message_count.fetch_add(1, Ordering::Relaxed);
                        while drain_rx.try_recv().is_ok() {}

                        let receipt = SendReceipt {
                            message_id: msg_id,
                            online_nodes,
                            matching_nodes,
                        };
                        let _ = respond_to.send(Ok(receipt));
                    }
                    Some(BusCommand::Connect {
                        info,
                        filter,
                        respond_to,
                    }) => {
                        let result = handle_connect(&broadcast_tx, &nodes, info, filter, bus_id);
                        while drain_rx.try_recv().is_ok() {}
                        let _ = respond_to.send(result);
                    }
                    Some(BusCommand::Disconnect {
                        node_id,
                        respond_to,
                    }) => {
                        handle_disconnect(&broadcast_tx, &nodes, &node_id, bus_id);
                        while drain_rx.try_recv().is_ok() {}
                        let _ = respond_to.send(());
                    }
                    Some(BusCommand::HeartbeatAck { node_id }) => {
                        if let Ok(mut map) = nodes.write()
                            && let Some(entry) = map.get_mut(&node_id)
                        {
                            entry.last_ack = Instant::now();
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
                heartbeat::handle_heartbeat_tick(&broadcast_tx, &nodes, heartbeat_timeout, bus_id);
                while drain_rx.try_recv().is_ok() {}
            }
        }
    }
}

/// Register a node and broadcast `node_online`.
fn handle_connect(
    broadcast_tx: &broadcast::Sender<Message>,
    nodes: &Arc<RwLock<HashMap<NodeId, NodeEntry>>>,
    info: NodeInfo,
    filter: MessageFilter,
    bus_id: BusId,
) -> Result<(), ConnectError> {
    let node_id = info.node_id.clone();

    // Check for duplicate
    {
        let map = nodes.read().unwrap();
        if map.contains_key(&node_id) {
            return Err(ConnectError::AlreadyConnected(node_id));
        }
    }

    // Register entry
    {
        let mut map = nodes.write().unwrap();
        map.insert(
            node_id.clone(),
            NodeEntry {
                info: info.clone(),
                last_ack: Instant::now(),
                filter,
            },
        );
    }

    // Broadcast node_online (stamped with this Bus's id)
    let online_msg = Message::with_from_bus(
        "node_online",
        node_id,
        vec![],
        serde_json::to_value(&info).unwrap_or_default(),
        bus_id,
    );
    let _ = broadcast_tx.send(online_msg);

    Ok(())
}

/// Remove a node and broadcast `node_offline`.
fn handle_disconnect(
    broadcast_tx: &broadcast::Sender<Message>,
    nodes: &Arc<RwLock<HashMap<NodeId, NodeEntry>>>,
    node_id: &NodeId,
    bus_id: BusId,
) {
    {
        let mut map = nodes.write().unwrap();
        map.remove(node_id);
    }

    // Broadcast node_offline (stamped with this Bus's id)
    let offline_msg = Message::with_from_bus(
        "node_offline",
        node_id.clone(),
        vec![],
        serde_json::json!({}),
        bus_id,
    );
    let _ = broadcast_tx.send(offline_msg);
}

// ═══════════════════════════════════════════════════════════════════
// Modules
// ═══════════════════════════════════════════════════════════════════

mod connection;
mod filter;
mod graph;
mod heartbeat;
pub use connection::NodeHandle;

// ═══════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::{Message, MessageFilter, NodeId, NodeInfo, ToMatch};
    use std::time::Duration;

    // Helper to create a bus with default test parameters
    fn test_bus() -> Bus {
        Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16)
    }

    fn test_msg(payload: serde_json::Value) -> Message {
        Message::new("test", NodeId::new("s"), vec![], payload)
    }

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

        // Register the target node so the check passes (task 1.5)
        let _target = bus
            .connect(
                NodeInfo {
                    node_id: NodeId::new("mcp/filesystem"),
                    node_type: "mcp".into(),
                    capabilities: serde_json::json!({}),
                    online_since: 0,
                },
                MessageFilter {
                    types: None,
                    to_match: ToMatch::All,
                },
            )
            .await
            .unwrap();

        let msg = Message::new(
            "tool_call",
            NodeId::new("engine"),
            vec![NodeId::new("mcp/filesystem")],
            serde_json::json!("do_thing"),
        );
        bus.send(msg).await.unwrap();

        // Both subscribers see it — CAN: all messages on the wire
        assert!(rx1.recv().await.is_ok());
        assert!(rx2.recv().await.is_ok());

        _target.disconnect().await;
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // from_bus stamping (Phase 6 task 6.0.3) — 3 tests
    // ═══════════════════════════════════════════════════════════════

    // [广播] Bus.connect 后 node_online.from_bus 指向该 Bus
    #[tokio::test]
    async fn node_online_stamped_with_from_bus() {
        let bus = test_bus();
        let mut rx = bus.subscribe();
        let _handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.from_bus, Some(bus.id));
        _handle.disconnect().await;
        bus.shutdown().await;
    }

    // [广播] Bus.disconnect 后 node_offline.from_bus 仍指向该 Bus
    #[tokio::test]
    async fn node_offline_stamped_with_from_bus() {
        let bus = test_bus();
        let mut rx = bus.subscribe();
        let handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();
        let _ = rx.recv().await.unwrap(); // drain node_online
        handle.disconnect().await;
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.msg_type, "node_offline");
        assert_eq!(msg.from_bus, Some(bus.id));
        bus.shutdown().await;
    }

    // [广播] Bus.send 自动给消息加 from_bus
    #[tokio::test]
    async fn user_send_stamps_from_bus() {
        let bus = test_bus();
        let mut rx = bus.subscribe();
        bus.send(test_msg(serde_json::json!(null))).await.unwrap();
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.from_bus, Some(bus.id));
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Send — 定向消息投递保证 (3 tests)
    // ═══════════════════════════════════════════════════════════════

    // [投递] 定向发送给在线节点 → 成功，matching_nodes=1
    #[tokio::test]
    async fn directed_send_to_online_node_succeeds() {
        let bus = test_bus();
        let sender = bus
            .connect(test_node_info("s"), test_filter())
            .await
            .unwrap();
        let _target = bus
            .connect(test_node_info("t"), test_filter())
            .await
            .unwrap();

        let receipt = sender
            .send("action", vec![NodeId::new("t")], serde_json::json!("hi"))
            .await
            .unwrap();
        assert_eq!(receipt.online_nodes, 2); // sender + target
        assert_eq!(receipt.matching_nodes, 1); // directed: only target

        sender.disconnect().await;
        _target.disconnect().await;
        bus.shutdown().await;
    }

    // [投递] 定向发送给不在线节点 → SendError::NodeOffline
    #[tokio::test]
    async fn directed_send_to_offline_node_fails() {
        let bus = test_bus();
        let sender = bus
            .connect(test_node_info("s"), test_filter())
            .await
            .unwrap();

        let result = sender
            .send(
                "action",
                vec![NodeId::new("ghost")],
                serde_json::json!("hi"),
            )
            .await;
        assert!(
            matches!(result, Err(SendError::NodeOffline(ref ids)) if ids.len() == 1 && ids[0].as_str() == "ghost")
        );

        sender.disconnect().await;
        bus.shutdown().await;
    }

    // [投递] 广播消息 matching_nodes=online_nodes
    #[tokio::test]
    async fn broadcast_message_matching_nodes_equals_online_nodes() {
        let bus = test_bus();
        let sender = bus
            .connect(test_node_info("s"), test_filter())
            .await
            .unwrap();
        let _n2 = bus
            .connect(test_node_info("n2"), test_filter())
            .await
            .unwrap();

        let receipt = sender
            .send("action", vec![], serde_json::json!("all"))
            .await
            .unwrap();
        assert_eq!(receipt.online_nodes, 2);
        assert_eq!(receipt.matching_nodes, 2); // broadcast: everyone

        sender.disconnect().await;
        _n2.disconnect().await;
        bus.shutdown().await;
    }

    // [投递] 定向多个目标全部在线 → 成功
    #[tokio::test]
    async fn directed_send_multi_target_all_online_succeeds() {
        let bus = test_bus();
        let sender = bus
            .connect(test_node_info("s"), test_filter())
            .await
            .unwrap();
        let _a = bus
            .connect(test_node_info("a"), test_filter())
            .await
            .unwrap();
        let _b = bus
            .connect(test_node_info("b"), test_filter())
            .await
            .unwrap();

        let receipt = sender
            .send(
                "action",
                vec![NodeId::new("a"), NodeId::new("b")],
                serde_json::json!("hi"),
            )
            .await
            .unwrap();
        assert_eq!(receipt.online_nodes, 3); // sender + a + b
        assert_eq!(receipt.matching_nodes, 2); // both targets online

        sender.disconnect().await;
        _a.disconnect().await;
        _b.disconnect().await;
        bus.shutdown().await;
    }

    // [投递] 定向多个目标全部不在线 → NodeOffline([a, b])
    #[tokio::test]
    async fn directed_send_all_targets_offline_fails() {
        let bus = test_bus();
        let sender = bus
            .connect(test_node_info("s"), test_filter())
            .await
            .unwrap();
        // Neither "x" nor "y" is connected

        let result = sender
            .send(
                "action",
                vec![NodeId::new("x"), NodeId::new("y")],
                serde_json::json!("hi"),
            )
            .await;
        assert!(matches!(result, Err(SendError::NodeOffline(ref ids)) if ids.len() == 2));

        sender.disconnect().await;
        bus.shutdown().await;
    }

    // [投递] 定向多个目标部分在线 → 成功广播
    #[tokio::test]
    async fn directed_send_partial_targets_online_succeeds() {
        let bus = test_bus();
        let sender = bus
            .connect(test_node_info("s"), test_filter())
            .await
            .unwrap();
        let _online = bus
            .connect(test_node_info("online"), test_filter())
            .await
            .unwrap();
        // "offline" is NOT connected

        let receipt = sender
            .send(
                "action",
                vec![NodeId::new("online"), NodeId::new("offline")],
                serde_json::json!("hi"),
            )
            .await
            .unwrap();
        assert_eq!(receipt.online_nodes, 2); // sender + online
        assert_eq!(receipt.matching_nodes, 1); // only "online" is online

        sender.disconnect().await;
        _online.disconnect().await;
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
        bus.send(test_msg(serde_json::json!("pre_shutdown")))
            .await
            .unwrap();
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
        let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 1);
        let mut rx = bus.subscribe();

        bus.send(test_msg(serde_json::json!("x"))).await.unwrap();
        assert_eq!(rx.recv().await.unwrap().payload, serde_json::json!("x"));

        bus.shutdown().await;
    }

    // [边界] slow receiver 导致 Lagged——环形缓冲区满，慢消费者丢消息
    #[tokio::test]
    async fn slow_receiver_gets_lagged_error() {
        let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 2);
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
        let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 1);
        assert_eq!(bus.message_count(), 0);
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Bus — 收发侧健康状态 (6 tests)
    // ═══════════════════════════════════════════════════════════════

    // [健康] 无应用订阅者时 send 仍成功（drain receiver 保活 + 不阻塞）
    #[tokio::test]
    async fn send_succeeds_with_no_application_subscribers() {
        let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 4);
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
        bus.send(test_msg(serde_json::json!("before")))
            .await
            .unwrap();
        assert!(rx1.recv().await.is_ok());
        assert!(rx2.recv().await.is_ok());

        // Drop rx1
        drop(rx1);

        // Send more messages — rx2 should still receive them
        bus.send(test_msg(serde_json::json!("after1")))
            .await
            .unwrap();
        bus.send(test_msg(serde_json::json!("after2")))
            .await
            .unwrap();

        assert_eq!(
            rx2.recv().await.unwrap().payload,
            serde_json::json!("after1")
        );
        assert_eq!(
            rx2.recv().await.unwrap().payload,
            serde_json::json!("after2")
        );

        bus.shutdown().await;
    }

    // [健康] 慢消费者不阻塞发送方——drain 保活，慢消费者自行 Lagged
    // tokio broadcast 的 drain receiver 拉高了 channel 的最慢位置，
    // 慢消费者不会造成背压——它只会收到 Lagged 通知。
    #[tokio::test]
    async fn slow_receiver_lagged_not_backpressured() {
        let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 2);
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
        let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 8);
        let _slow_rx = bus.subscribe(); // never reads
        let mut fast_rx = bus.subscribe(); // reads promptly

        // Send within capacity — fast receiver gets all messages
        for i in 0..5 {
            bus.send(test_msg(serde_json::json!(i))).await.unwrap();
        }

        for i in 0..5 {
            assert_eq!(fast_rx.recv().await.unwrap().payload, serde_json::json!(i));
        }

        bus.shutdown().await;
    }

    // [健康] Lagged 后 receiver 可恢复接收新消息——跳过 lag，继续消费
    #[tokio::test]
    async fn receiver_recovers_after_lag() {
        let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 2);
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
        bus.send(test_msg(serde_json::json!("after_lag")))
            .await
            .unwrap();
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
                    let msg = Message::new("t", NodeId::new("s"), vec![], serde_json::json!(i));
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
        let bus =
            std::sync::Arc::into_inner(bus).expect("all other Arc references should be dropped");
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // 资源泄漏检测 (7 tests)
    // ═══════════════════════════════════════════════════════════════

    // [泄漏] NodeHandle drop 不调用 disconnect → NodeEntry 残留在 nodes map 中
    #[tokio::test]
    async fn handle_drop_without_disconnect_leaves_zombie_entry() {
        let bus = test_bus();

        // Connect and immediately drop the handle (simulates crash)
        {
            let _handle = bus
                .connect(test_node_info("crash-victim"), test_filter())
                .await
                .unwrap();
            // _handle dropped here — no disconnect()
        }

        // NodeEntry 仍然在 nodes map 中（graph 可见）
        let g = bus.graph();
        let zombie = g
            .nodes
            .iter()
            .find(|n| n.node_id.as_str() == "crash-victim");
        assert!(
            zombie.is_some(),
            "BUG: dropped handle was immediately removed — zombie entry should persist until heartbeat timeout"
        );

        // 尝试用相同 NodeId 重连 → 应被拒绝（已有 zombie entry）
        let result = bus
            .connect(test_node_info("crash-victim"), test_filter())
            .await;
        assert!(
            matches!(result, Err(ConnectError::AlreadyConnected(_))),
            "BUG: duplicate NodeId should be rejected while zombie entry exists"
        );

        bus.shutdown().await;
    }

    // [泄漏] zombie entry 在心跳超时后被清理
    #[tokio::test]
    async fn zombie_entry_cleaned_by_heartbeat_timeout() {
        let bus = Bus::new(
            Duration::from_millis(20), // fast heartbeat
            Duration::from_millis(60), // short timeout
            16,
        );

        // 连一个正常节点用于观察
        let mut watcher = bus
            .connect(
                NodeInfo {
                    node_id: NodeId::new("watcher"),
                    node_type: "test".into(),
                    capabilities: serde_json::json!({}),
                    online_since: 0,
                },
                MessageFilter {
                    types: None,
                    to_match: ToMatch::All,
                },
            )
            .await
            .unwrap();

        // 连一个会 drop 的节点（不调用 disconnect）
        {
            let _zombie = bus
                .connect(
                    NodeInfo {
                        node_id: NodeId::new("zombie"),
                        node_type: "test".into(),
                        capabilities: serde_json::json!({}),
                        online_since: 0,
                    },
                    test_filter(),
                )
                .await
                .unwrap();
            // _zombie dropped → NodeEntry 残留，但不再发送 HeartbeatAck
        }

        // Drain node_online (watcher 会收到 zombie 的 node_online)
        let _ = watcher.recv().await.unwrap(); // zombie's node_online

        // 等待心跳超时清理 — zombie 的 last_ack 在 connect 时设定，
        // 经过 3 个 tick（20ms×3=60ms）+ timeout 判定（>60ms）
        // → 第四个 tick（~80ms）时 node_offline 被广播。
        // 使用单一长超时让 recv() 持续等待（内部循环会过滤 heartbeat_request）。
        let result = tokio::time::timeout(Duration::from_millis(500), watcher.recv()).await;
        let mut saw_offline = false;
        if let Ok(Ok(msg)) = result {
            saw_offline = msg.msg_type == "node_offline" && msg.from.as_str() == "zombie";
        }

        assert!(
            saw_offline,
            "BUG: zombie node should be cleaned by heartbeat timeout and broadcast node_offline"
        );

        // graph 不再包含 zombie
        let g = bus.graph();
        let zombie = g.nodes.iter().find(|n| n.node_id.as_str() == "zombie");
        assert!(
            zombie.is_none(),
            "BUG: zombie entry not cleaned from nodes map"
        );

        // zombie 被清理后，同 NodeId 可重连
        let h = bus
            .connect(
                NodeInfo {
                    node_id: NodeId::new("zombie"),
                    node_type: "test".into(),
                    capabilities: serde_json::json!({}),
                    online_since: 0,
                },
                test_filter(),
            )
            .await;
        assert!(
            h.is_ok(),
            "BUG: should be able to reconnect after zombie cleaned"
        );

        watcher.disconnect().await;
        h.unwrap().disconnect().await;
        bus.shutdown().await;
    }

    // [清理] signal_shutdown 后 broadcast channel 关闭 → recv 返回 Closed
    #[tokio::test]
    async fn signal_shutdown_closes_broadcast_channel() {
        let bus = Arc::new(Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16));
        let mut handle = bus
            .connect(test_node_info("victim"), test_filter())
            .await
            .unwrap();

        // 用 signal_shutdown（Python 侧 bus.shutdown() 的实际行为）
        bus.signal_shutdown();

        // recv 应立即返回 Closed（broadcast channel 已关闭），不应阻塞
        let result = handle.recv().await;
        assert!(
            matches!(result, Err(broadcast::error::RecvError::Closed)),
            "BUG: signal_shutdown should close broadcast channel, got {result:?}"
        );

        // send 也应返回 BusClosed
        let send_result = handle.send("t", vec![], serde_json::json!(null)).await;
        assert!(
            matches!(send_result, Err(SendError::BusClosed)),
            "BUG: send after signal_shutdown should return BusClosed"
        );
    }

    // [清理] 正常 disconnect → NodeEntry 立即从 map 移除
    #[tokio::test]
    async fn disconnect_immediately_removes_entry() {
        let bus = test_bus();
        let handle = bus
            .connect(test_node_info("clean-exit"), test_filter())
            .await
            .unwrap();

        assert_eq!(bus.graph().nodes.len(), 1);
        handle.disconnect().await;

        // disconnect 后立即从 graph 消失
        let g = bus.graph();
        assert_eq!(
            g.nodes.len(),
            0,
            "BUG: disconnected node still in graph: {:?}",
            g.nodes
        );

        // 同 NodeId 可立即重连
        let h2 = bus
            .connect(test_node_info("clean-exit"), test_filter())
            .await;
        assert!(
            h2.is_ok(),
            "BUG: should reconnect immediately after disconnect"
        );

        h2.unwrap().disconnect().await;
        bus.shutdown().await;
    }

    // [泄漏] Bus drop 不调用 shutdown → spawned task 正常退出
    #[tokio::test]
    async fn bus_drop_without_shutdown_task_exits() {
        let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);

        // Connect a node to create some internal state
        let handle = bus
            .connect(test_node_info("n"), test_filter())
            .await
            .unwrap();

        // Get the JoinHandle before dropping (we can't, it's private)
        // Instead: drop the bus and verify no panic via timeout
        handle.disconnect().await;
        drop(bus);

        // 给 spawned task 一点时间退出
        tokio::time::sleep(Duration::from_millis(50)).await;

        // 没有 panic，没有 hang — 测试通过
    }

    // [泄漏] 反复 connect/disconnect 同 NodeId — nodes map 不累积
    #[tokio::test]
    async fn repeated_connect_disconnect_no_accumulation() {
        let bus = test_bus();

        for _ in 0..20 {
            let h = bus
                .connect(test_node_info("flappy"), test_filter())
                .await
                .unwrap();
            // graph 中始终只有 flappy 一个节点
            let g = bus.graph();
            let count = g
                .nodes
                .iter()
                .filter(|n| n.node_id.as_str() == "flappy")
                .count();
            assert_eq!(count, 1, "BUG: duplicate entries for same NodeId");
            h.disconnect().await;

            // disconnect 后 flappy 不在 graph 中
            let g2 = bus.graph();
            assert!(
                !g2.nodes.iter().any(|n| n.node_id.as_str() == "flappy"),
                "BUG: entry not removed after disconnect"
            );
        }

        bus.shutdown().await;
    }

    // [泄漏] 所有 NodeHandle disconnect 后 nodes map 应为空
    #[tokio::test]
    async fn nodes_map_empty_after_all_disconnected() {
        let bus = test_bus();

        let mut handles = Vec::new();
        for i in 0..5 {
            let h = bus
                .connect(
                    NodeInfo {
                        node_id: NodeId::new(format!("node-{i}")),
                        node_type: "test".into(),
                        capabilities: serde_json::json!({}),
                        online_since: 0,
                    },
                    test_filter(),
                )
                .await
                .unwrap();
            handles.push(h);
        }

        assert_eq!(bus.graph().nodes.len(), 5);

        // Disconnect all
        for h in handles {
            h.disconnect().await;
        }

        let g = bus.graph();
        assert!(
            g.nodes.is_empty(),
            "BUG: nodes map not empty after all disconnected: {} nodes remain",
            g.nodes.len()
        );

        bus.shutdown().await;
    }
}
