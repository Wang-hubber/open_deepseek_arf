//! Node connection lifecycle — connect, send, receive, disconnect.
//!
//! `NodeHandle` is the primary API that nodes use to interact with one or
//! more Buses. Phase 6 task 6.0.2 introduces multi-Bus subscription via
//! `attach_to()`. The primary Bus is set by `Bus::connect()`.
//!
//! ## Multi-Bus architecture
//!
//! Each subscription has its own inbound `mpsc` channel. The forwarding task
//! (one per subscription) writes filtered, heartbeat-acked messages to its
//! channel; `NodeHandle::recv()` uses `futures::future::select_all` over all
//! active subscription receivers. When all forwarding tasks exit (all attached
//! Buses shut down), all inbound channels close and `recv()` returns
//! `Err(RecvError::Closed)`.

use arf_core::{
    BusId, Message, MessageFilter, NodeId, NodeInfo, SendError, SendReceipt,
};
use futures::future::{self, FutureExt};
use std::pin::Pin;
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, oneshot};
use uuid::Uuid;

use crate::{Bus, BusCommand, ConnectError};

// ═══════════════════════════════════════════════════════════════════
// Subscription — per-Bus state owned by NodeHandle
// ═══════════════════════════════════════════════════════════════════

/// One Bus subscription inside a `NodeHandle`.
///
/// Created by `Bus::connect()` (primary) or `NodeHandle::attach_to()` (additional).
/// Each subscription runs a forwarding task that intercepts heartbeats, applies
/// the per-subscription filter, and writes accepted messages to its own
/// inbound `mpsc`. `NodeHandle::recv()` selects over all subscription inbounds.
pub struct Subscription {
    /// Stable identifier for the Bus this subscription is attached to.
    pub bus_id: BusId,
    /// Channel for sending commands (Disconnect, HeartbeatAck) back to this Bus.
    pub(crate) cmd_tx: mpsc::Sender<BusCommand>,
    /// Bus-side broadcast receiver (held for API symmetry; the actual `recv`
    /// loop runs in the per-subscription forwarding task).
    pub(crate) _bus_broadcast_rx: broadcast::Receiver<Message>,
    /// Per-subscription inbound receiver. Forwarding task sends filtered
    /// messages here; NodeHandle reads via select_all across all subs.
    pub(crate) inbound_rx: mpsc::Receiver<Message>,
    /// Per-subscription message filter.
    pub filter: MessageFilter,
}

// ═══════════════════════════════════════════════════════════════════
// NodeHandle
// ═══════════════════════════════════════════════════════════════════

/// A node's handle to one or more Buses.
///
/// Created by `Bus::connect()` (primary subscription), extended by
/// `NodeHandle::attach_to()` (additional subscriptions). `recv()` reads the
/// next message from any subscription via `select_all`. Closing semantics:
/// `recv()` returns `Err(Closed)` only when ALL attached Buses' forwarding
/// tasks have exited (all per-subscription inbound channels closed).
///
/// `recv()` requires `&mut self` because it advances receiver cursors.
pub struct NodeHandle {
    /// This node's identity and capabilities (from primary connect()).
    pub(crate) info: NodeInfo,
    /// Primary Bus identifier — used by `send()` to choose a default route.
    pub(crate) primary_bus_id: BusId,
    /// All subscriptions (primary first, then attached in attach order).
    pub(crate) subscriptions: Vec<Subscription>,
}

impl NodeHandle {
    /// Send a message from this node on the **primary** Bus.
    ///
    /// Equivalent to `send_via(self.primary_bus_id, ...)`. To target a specific
    /// attached Bus, use `send_via`.
    pub async fn send(
        &self,
        msg_type: &str,
        to: Vec<NodeId>,
        payload: serde_json::Value,
    ) -> Result<SendReceipt, SendError> {
        self.send_via(self.primary_bus_id, msg_type, to, payload).await
    }

    /// Send a response message that includes a correlation_id in the payload.
    ///
    /// The Engine's wait_for_strategy matches responses by `payload.correlation_id`,
    /// so response payloads (model_response, tool_result, app_checkpoint_result, …)
    /// MUST include the same correlation_id as the originating request.
    /// `send()` does NOT add correlation_id; use `send_response` instead.
    ///
    /// If `correlation_id` is `None`, behaves identically to [`send`].
    pub async fn send_response(
        &self,
        msg_type: &str,
        to: Vec<NodeId>,
        mut payload: serde_json::Value,
        correlation_id: Option<uuid::Uuid>,
    ) -> Result<SendReceipt, SendError> {
        if let Some(cid) = correlation_id {
            if let Some(obj) = payload.as_object_mut() {
                obj.insert("correlation_id".into(), serde_json::Value::String(cid.to_string()));
            }
        }
        self.send(msg_type, to, payload).await
    }

    /// Public access to primary BusId (for constructing `Message::from_bus`
    /// outside the handle — e.g., `Engine` building outgoing messages).
    pub fn primary_bus_id(&self) -> BusId {
        self.primary_bus_id
    }

    /// Send a pre-constructed `Message` via this handle's primary Bus.
    ///
    /// The `from_bus` field on the message is automatically set to
    /// `self.primary_bus_id` (Phase 6 task 6.0.3 stamping); `msg.from`
    /// is left as-is so callers can stamp their own node id.
    pub async fn send_message(&self, msg: Message) -> Result<SendReceipt, SendError> {
        self.send_via(
            self.primary_bus_id,
            msg.msg_type.as_str(),
            msg.to.clone(),
            msg.payload.clone(),
        )
        .await
    }

    /// Send a message on a specific attached Bus.
    ///
    /// Returns `SendError::NoSuchBus` if the BusId is not among the attached
    /// subscriptions.
    pub async fn send_via(
        &self,
        bus_id: BusId,
        msg_type: &str,
        to: Vec<NodeId>,
        payload: serde_json::Value,
    ) -> Result<SendReceipt, SendError> {
        let sub = self
            .subscriptions
            .iter()
            .find(|s| s.bus_id == bus_id)
            .ok_or(SendError::NoSuchBus(bus_id))?;
        let msg = Message::with_from_bus(
            msg_type,
            self.info.node_id.clone(),
            to,
            payload,
            bus_id,
        );
        let (tx, rx) = oneshot::channel();
        sub.cmd_tx
            .send(BusCommand::Send { msg, respond_to: tx })
            .await
            .map_err(|_| SendError::BusClosed)?;
        rx.await.map_err(|_| SendError::BusClosed)?
    }

    /// Attach this handle to another Bus.
    ///
    /// Adds a subscription with its own filter. The same NodeId may be attached
    /// to multiple Buses; each Bus maintains its own online-nodes map.
    pub async fn attach_to(
        &mut self,
        bus: Arc<Bus>,
        filter: MessageFilter,
    ) -> Result<BusId, ConnectError> {
        let bus_id = bus.id;

        // Register in the new Bus's nodes map.
        let (reg_tx, reg_rx) = oneshot::channel();
        bus.cmd_tx
            .send(BusCommand::Connect {
                info: self.info.clone(),
                filter: filter.clone(),
                respond_to: reg_tx,
            })
            .await
            .map_err(|_| ConnectError::BusClosed)?;
        reg_rx.await.map_err(|_| ConnectError::BusClosed)??;

        // Subscribe AFTER registration so we don't see our own node_online.
        let forward_rx = bus.subscribe_internal();
        let stored_rx = bus.subscribe_internal();

        // Each subscription gets its own inbound mpsc. The forwarding task owns
        // the tx; NodeHandle holds the rx as this subscription's `inbound_rx`.
        let (inbound_tx, inbound_rx) = mpsc::channel(16);
        tokio::spawn(spawn_forward_task(
            bus.cmd_tx.clone(),
            forward_rx,
            filter.clone(),
            self.info.node_id.clone(),
            inbound_tx,
        ));

        self.subscriptions.push(Subscription {
            bus_id,
            cmd_tx: bus.cmd_tx.clone(),
            _bus_broadcast_rx: stored_rx,
            inbound_rx,
            filter,
        });

        Ok(bus_id)
    }

    /// Receive the next application-visible message from any attached Bus.
    ///
    /// Heartbeat requests are intercepted by per-subscription forwarding tasks
    /// and auto-acknowledged. Per-subscription `MessageFilter` is applied before
    /// messages reach this recv. Returns `Err(Closed)` when ALL attached Buses
    /// have shut down (all forwarding tasks exited, all inbound channels closed).
    pub async fn recv(&mut self) -> Result<Message, broadcast::error::RecvError> {
        loop {
            // First, drain anything immediately available across all subscriptions.
            for sub in self.subscriptions.iter_mut() {
                match sub.inbound_rx.try_recv() {
                    Ok(msg) => return Ok(msg),
                    Err(mpsc::error::TryRecvError::Empty) => continue,
                    Err(mpsc::error::TryRecvError::Disconnected) => continue,
                }
            }

            // Check if all subscriptions have closed (no active forwarding tasks).
            let active = self
                .subscriptions
                .iter()
                .filter(|s| !s.inbound_rx.is_closed())
                .count();
            if active == 0 {
                return Err(broadcast::error::RecvError::Closed);
            }

            // Build await-futures for non-closed subscriptions only.
            let mut futures: Vec<Pin<Box<dyn future::Future<Output = Option<Message>> + Send>>> =
                Vec::new();
            for sub in self.subscriptions.iter_mut() {
                if !sub.inbound_rx.is_closed() {
                    futures.push(sub.inbound_rx.recv().boxed());
                }
            }
            if futures.is_empty() {
                return Err(broadcast::error::RecvError::Closed);
            }

            let (result, _idx, _rest) = future::select_all(futures).await;
            match result {
                Some(msg) => return Ok(msg),
                None => continue, // One subscription closed; loop to check others.
            }
        }
    }

    /// Try to receive a message without blocking.
    ///
    /// Returns `Ok(None)` if no message is ready across all subscriptions,
    /// `Err(Closed)` if all forwarding tasks have exited (all Buses shut down).
    pub fn try_recv(&mut self) -> Result<Option<Message>, broadcast::error::TryRecvError> {
        let mut any_alive = false;
        for sub in self.subscriptions.iter_mut() {
            match sub.inbound_rx.try_recv() {
                Ok(msg) => return Ok(Some(msg)),
                Err(mpsc::error::TryRecvError::Empty) => {
                    if !sub.inbound_rx.is_closed() {
                        any_alive = true;
                    }
                }
                Err(mpsc::error::TryRecvError::Disconnected) => continue,
            }
        }
        if any_alive {
            Ok(None)
        } else {
            Err(broadcast::error::TryRecvError::Closed)
        }
    }

    /// Get this node's identity and capabilities.
    pub fn node_info(&self) -> &NodeInfo {
        &self.info
    }

    /// Get the primary Bus's filter configuration.
    pub fn filter_config(&self) -> &MessageFilter {
        &self.subscriptions[0].filter
    }

    /// List attached BusIds (primary first, then attached in attach order).
    pub fn subscriptions(&self) -> Vec<BusId> {
        self.subscriptions.iter().map(|s| s.bus_id).collect()
    }

    /// Disconnect from all attached Buses.
    ///
    /// Sends `Disconnect` to each attached Bus (in attach order), then drops
    /// `self` — which closes all inbound `mpsc::Receiver`s and causes the
    /// forwarding tasks to exit on their next iteration.
    pub async fn disconnect(self) {
        for sub in &self.subscriptions {
            let (tx, rx) = oneshot::channel();
            let _ = sub
                .cmd_tx
                .send(BusCommand::Disconnect {
                    node_id: self.info.node_id.clone(),
                    respond_to: tx,
                })
                .await;
            let _ = rx.await;
        }
        // Drop self; all subscription inbound_rx are dropped, forwarding
        // tasks exit on next `tx.send().await` failure.
    }

    /// Respond to a barrier request via the primary subscription's Bus.
    ///
    /// The ack carries the given `correlation_id` so the Bus can match it
    /// to the originating `Bus::barrier()` call.
    ///
    /// Returns `SendError::BusClosed` if the primary Bus is shut down.
    pub async fn barrier_ack(&self, correlation_id: Uuid) -> Result<(), SendError> {
        let msg = Message::with_from_bus(
            "barrier_ack",
            self.info.node_id.clone(),
            vec![],
            serde_json::json!({ "correlation_id": correlation_id }),
            self.primary_bus_id,
        );
        let (tx, rx) = oneshot::channel();
        let primary = self
            .subscriptions
            .first()
            .expect("NodeHandle always has at least one subscription");
        primary
            .cmd_tx
            .send(BusCommand::Send { msg, respond_to: tx })
            .await
            .map_err(|_| SendError::BusClosed)?;
        rx.await
            .map_err(|_| SendError::BusClosed)?
            .map(|_| ())
    }
}

// ═══════════════════════════════════════════════════════════════════
// Forwarding task — one per subscription
// ═══════════════════════════════════════════════════════════════════

/// Background task: read broadcast messages from one subscription, intercept
/// heartbeats, apply the per-subscription filter, and forward accepted messages
/// to the per-subscription inbound `mpsc`.
///
/// Exits when:
/// - broadcast channel closes (Bus shutdown / disconnect), OR
/// - inbound `mpsc` closes (NodeHandle dropped).
///
/// `Lagged` is silently skipped — the slow consumer loses the lagged messages
/// without affecting the NodeHandle API (handle.recv() doesn't expose Lagged).
async fn spawn_forward_task(
    cmd_tx: mpsc::Sender<BusCommand>,
    mut broadcast_rx: broadcast::Receiver<Message>,
    filter: MessageFilter,
    node_id: NodeId,
    inbound_tx: mpsc::Sender<Message>,
) {
    loop {
        // Check if NodeHandle has been dropped. If yes, stop acking heartbeats
        // so the Bus can mark this node offline (Phase 6 task 6.0.2 semantic).
        if inbound_tx.is_closed() {
            break;
        }
        match broadcast_rx.recv().await {
            Ok(msg) => {
                if msg.msg_type == "heartbeat_request" {
                    let _ = cmd_tx
                        .send(BusCommand::HeartbeatAck {
                            node_id: node_id.clone(),
                        })
                        .await;
                    continue;
                }
                if !filter.matches(&msg, &node_id) {
                    continue;
                }
                if inbound_tx.send(msg).await.is_err() {
                    break;
                }
            }
            Err(broadcast::error::RecvError::Lagged(_)) => continue,
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// Bus — connect
// ═══════════════════════════════════════════════════════════════════

impl Bus {
    /// Connect a node to this Bus. Returns a `NodeHandle` with one subscription.
    ///
    /// The returned `NodeHandle`'s forwarding task subscribes **after**
    /// registration, so the node does not see its own `node_online` message.
    ///
    /// Returns `Err(ConnectError::AlreadyConnected)` if a node with the same
    /// `NodeId` is already online on this Bus.
    pub async fn connect(
        &self,
        info: NodeInfo,
        filter: MessageFilter,
    ) -> Result<NodeHandle, ConnectError> {
        let bus_id = self.id;

        // Register in the nodes map.
        let (reg_tx, reg_rx) = oneshot::channel();
        self.cmd_tx
            .send(BusCommand::Connect {
                info: info.clone(),
                filter: filter.clone(),
                respond_to: reg_tx,
            })
            .await
            .map_err(|_| ConnectError::BusClosed)?;
        reg_rx.await.map_err(|_| ConnectError::BusClosed)??;

        // Subscribe AFTER registration — the forwarding task gets the real
        // working receiver; Subscription stores a separate one for symmetry.
        let forward_rx = self.subscribe_internal();
        let stored_rx = self.subscribe_internal();

        // Per-subscription inbound mpsc. forwarding task owns the tx.
        let (inbound_tx, inbound_rx) = mpsc::channel(16);
        tokio::spawn(spawn_forward_task(
            self.cmd_tx.clone(),
            forward_rx,
            filter.clone(),
            info.node_id.clone(),
            inbound_tx,
        ));

        let primary_sub = Subscription {
            bus_id,
            cmd_tx: self.cmd_tx.clone(),
            _bus_broadcast_rx: stored_rx,
            inbound_rx,
            filter,
        };

        Ok(NodeHandle {
            info,
            primary_bus_id: bus_id,
            subscriptions: vec![primary_sub],
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
    use std::sync::Arc;
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

    fn test_bus() -> Bus {
        Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16)
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeHandle — 构造 & 基本属性 (5 tests)
    // ═══════════════════════════════════════════════════════════════

    // [构造] connect 返回 NodeHandle，node_info() 返回传入的 NodeInfo
    #[tokio::test]
    async fn connect_returns_node_handle_with_correct_info() {
        let bus = test_bus();
        let info = test_node_info("node-1");
        let handle = bus.connect(info, test_filter()).await.unwrap();
        assert_eq!(handle.node_info().node_id.as_str(), "node-1");
        assert_eq!(handle.node_info().node_type, "test");
        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [构造] connect 后 node_online 被广播
    #[tokio::test]
    async fn connect_broadcasts_node_online() {
        let bus = test_bus();
        let mut rx = bus.subscribe();
        let info = test_node_info("node-a");
        let handle = bus.connect(info, test_filter()).await.unwrap();
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.msg_type, "node_online");
        assert_eq!(msg.from.as_str(), "node-a");
        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [构造] filter_config() 返回 primary subscription 的 MessageFilter
    #[tokio::test]
    async fn node_handle_stores_filter_config() {
        let bus = test_bus();
        let filter = MessageFilter {
            types: Some(vec!["model_call".into()]),
            to_match: ToMatch::DirectedToMe,
        };
        let handle = bus
            .connect(test_node_info("n1"), filter.clone())
            .await
            .unwrap();
        assert_eq!(handle.filter_config().types, filter.types);
        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [错误] 重复 NodeId connect → AlreadyConnected
    #[tokio::test]
    async fn connect_duplicate_node_id_returns_already_connected() {
        let bus = test_bus();
        let info = test_node_info("dup");
        let _h1 = bus.connect(info.clone(), test_filter()).await.unwrap();
        let result = bus.connect(info, test_filter()).await;
        assert!(matches!(result, Err(ConnectError::AlreadyConnected(_))));
        _h1.disconnect().await;
        bus.shutdown().await;
    }

    // [构造] subscriptions() 初始仅含 primary BusId
    #[tokio::test]
    async fn subscriptions_initially_only_primary() {
        let bus = test_bus();
        let handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();
        assert_eq!(handle.subscriptions(), vec![bus.id]);
        handle.disconnect().await;
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeHandle — send & recv (5 tests)
    // ═══════════════════════════════════════════════════════════════

    // [数据] NodeHandle.send() → 其他 subscriber 收到消息，from 自动填充
    #[tokio::test]
    async fn node_handle_send_message_appears_on_bus() {
        let bus = test_bus();
        let mut rx = bus.subscribe();
        let sender = bus
            .connect(test_node_info("sender"), test_filter())
            .await
            .unwrap();
        let _ = rx.recv().await.unwrap();

        let receipt = sender
            .send("action", vec![], serde_json::json!({"cmd": "run"}))
            .await
            .unwrap();
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.msg_type, "action");
        assert_eq!(msg.from.as_str(), "sender");
        assert_eq!(msg.payload, serde_json::json!({"cmd": "run"}));
        assert_eq!(msg.id, receipt.message_id);

        sender.disconnect().await;
        bus.shutdown().await;
    }

    // [数据] SendReceipt.online_nodes 反映在线节点数
    #[tokio::test]
    async fn send_receipt_counts_online_nodes() {
        let bus = test_bus();
        let sender = bus
            .connect(test_node_info("s"), test_filter())
            .await
            .unwrap();
        let receipt = sender
            .send("t", vec![], serde_json::json!(null))
            .await
            .unwrap();
        assert_eq!(receipt.online_nodes, 1);

        let other = bus
            .connect(test_node_info("o"), test_filter())
            .await
            .unwrap();
        let receipt2 = sender
            .send("t", vec![], serde_json::json!(null))
            .await
            .unwrap();
        assert_eq!(receipt2.online_nodes, 2);

        sender.disconnect().await;
        other.disconnect().await;
        bus.shutdown().await;
    }

    // [数据] NodeHandle.recv() 收到其他节点发送的消息
    #[tokio::test]
    async fn node_handle_recv_receives_from_others() {
        let bus = test_bus();
        let mut receiver = bus
            .connect(test_node_info("receiver"), test_filter())
            .await
            .unwrap();
        let sender = bus
            .connect(test_node_info("sender2"), test_filter())
            .await
            .unwrap();
        sender
            .send("ping", vec![], serde_json::json!("hello"))
            .await
            .unwrap();
        let msg = receiver.recv().await.unwrap();
        if msg.msg_type == "node_online" {
            let msg2 = receiver.recv().await.unwrap();
            assert_eq!(msg2.msg_type, "ping");
        } else {
            assert_eq!(msg.msg_type, "ping");
        }
        receiver.disconnect().await;
        sender.disconnect().await;
        bus.shutdown().await;
    }

    // [数据] try_recv() 无消息时返回 Ok(None)
    #[tokio::test]
    async fn try_recv_returns_none_when_empty() {
        let bus = test_bus();
        let mut handle = bus
            .connect(test_node_info("n"), test_filter())
            .await
            .unwrap();
        let result = handle.try_recv().unwrap();
        assert!(result.is_none());
        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [数据] recv() 按发送顺序收到消息
    #[tokio::test]
    async fn recv_receives_messages_in_order() {
        let bus = test_bus();
        let mut receiver = bus
            .connect(test_node_info("recv"), test_filter())
            .await
            .unwrap();
        let sender = bus
            .connect(test_node_info("send"), test_filter())
            .await
            .unwrap();
        let _ = receiver.recv().await.unwrap();

        for i in 0..5 {
            sender
                .send("seq", vec![], serde_json::json!(i))
                .await
                .unwrap();
        }
        for i in 0..5 {
            let msg = receiver.recv().await.unwrap();
            assert_eq!(msg.payload, serde_json::json!(i));
        }
        receiver.disconnect().await;
        sender.disconnect().await;
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeHandle — disconnect (3 tests)
    // ═══════════════════════════════════════════════════════════════

    // [生命周期] disconnect() 后 node_offline 被广播
    #[tokio::test]
    async fn disconnect_broadcasts_node_offline() {
        let bus = test_bus();
        let mut rx = bus.subscribe();
        let handle = bus
            .connect(test_node_info("ephemeral"), test_filter())
            .await
            .unwrap();
        let _ = rx.recv().await.unwrap();
        handle.disconnect().await;
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.msg_type, "node_offline");
        assert_eq!(msg.from.as_str(), "ephemeral");
        bus.shutdown().await;
    }

    // [生命周期] disconnect 后同一 NodeId 可重新 connect
    #[tokio::test]
    async fn reconnect_after_disconnect_succeeds() {
        let bus = test_bus();
        let info = test_node_info("reconnector");
        let h1 = bus.connect(info.clone(), test_filter()).await.unwrap();
        h1.disconnect().await;
        let h2 = bus.connect(info, test_filter()).await.unwrap();
        assert_eq!(h2.node_info().node_id.as_str(), "reconnector");
        h2.disconnect().await;
        bus.shutdown().await;
    }

    // [生命周期] reconnect 后 send 正常
    #[tokio::test]
    async fn reconnect_send_works() {
        let bus = test_bus();
        let mut rx = bus.subscribe();
        let info = test_node_info("r");
        let h1 = bus.connect(info.clone(), test_filter()).await.unwrap();
        h1.disconnect().await;
        let _ = rx.recv().await.unwrap();
        let _ = rx.recv().await.unwrap();
        let h2 = bus.connect(info, test_filter()).await.unwrap();
        h2.send("after_reconnect", vec![], serde_json::json!("ok"))
            .await
            .unwrap();
        let _ = rx.recv().await.unwrap();
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.msg_type, "after_reconnect");
        h2.disconnect().await;
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeHandle — 多节点 (3 tests)
    // ═══════════════════════════════════════════════════════════════

    // [多节点] 两个节点互通
    #[tokio::test]
    async fn two_nodes_can_exchange_messages() {
        let bus = test_bus();
        let mut a = bus
            .connect(test_node_info("A"), test_filter())
            .await
            .unwrap();
        let mut b = bus
            .connect(test_node_info("B"), test_filter())
            .await
            .unwrap();
        let msg = a.recv().await.unwrap();
        assert_eq!(msg.msg_type, "node_online");
        assert_eq!(msg.from.as_str(), "B");

        a.send("chat", vec![], serde_json::json!("from_a"))
            .await
            .unwrap();
        b.send("chat", vec![], serde_json::json!("from_b"))
            .await
            .unwrap();

        let a_first = a.recv().await.unwrap();
        let a_second = a.recv().await.unwrap();
        assert_eq!(a_first.payload, serde_json::json!("from_a"));
        assert_eq!(a_second.payload, serde_json::json!("from_b"));

        let b_first = b.recv().await.unwrap();
        let b_second = b.recv().await.unwrap();
        assert_eq!(b_first.payload, serde_json::json!("from_a"));
        assert_eq!(b_second.payload, serde_json::json!("from_b"));

        a.disconnect().await;
        b.disconnect().await;
        bus.shutdown().await;
    }

    // [多节点] 新节点看不到 connect 之前的历史消息
    #[tokio::test]
    async fn late_joiner_sees_only_future_messages() {
        let bus = test_bus();
        let early = bus
            .connect(test_node_info("early"), test_filter())
            .await
            .unwrap();
        early
            .send("historical", vec![], serde_json::json!("ancient"))
            .await
            .unwrap();
        let mut late = bus
            .connect(test_node_info("late"), test_filter())
            .await
            .unwrap();
        early
            .send("current", vec![], serde_json::json!("now"))
            .await
            .unwrap();
        let msg = late.recv().await.unwrap();
        assert_eq!(msg.msg_type, "current");
        early.disconnect().await;
        late.disconnect().await;
        bus.shutdown().await;
    }

    // [多节点] 节点 disconnect 后其他节点不受影响
    #[tokio::test]
    async fn surviving_node_unaffected_by_other_disconnect() {
        let bus = test_bus();
        let survivor = bus
            .connect(test_node_info("survivor"), test_filter())
            .await
            .unwrap();
        let leaver = bus
            .connect(test_node_info("leaver"), test_filter())
            .await
            .unwrap();
        leaver.disconnect().await;
        let receipt = survivor
            .send("still_here", vec![], serde_json::json!("ok"))
            .await
            .unwrap();
        assert_eq!(receipt.online_nodes, 1);
        survivor.disconnect().await;
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeHandle — shutdown 交互 (2 tests)
    // ═══════════════════════════════════════════════════════════════

    // [关闭] shutdown 后 receiver 返回 Closed
    #[tokio::test]
    async fn node_handle_recv_returns_closed_after_shutdown() {
        let bus = test_bus();
        let mut handle = bus
            .connect(test_node_info("n"), test_filter())
            .await
            .unwrap();
        bus.shutdown().await;
        // Use timeout because forwarding task needs a brief moment to detect
        // broadcast close and exit, then handle.recv() observes Closed.
        let result = tokio::time::timeout(Duration::from_secs(2), handle.recv())
            .await
            .expect("recv should observe Closed within 2s")
            .unwrap_err();
        assert!(matches!(result, broadcast::error::RecvError::Closed));
    }

    // [关闭] shutdown 后 NodeHandle.send() 返回 BusClosed
    #[tokio::test]
    async fn node_handle_send_after_shutdown_returns_bus_closed() {
        let bus = test_bus();
        let handle = bus
            .connect(test_node_info("n"), test_filter())
            .await
            .unwrap();
        bus.shutdown().await;
        let result = handle.send("t", vec![], serde_json::json!(null)).await;
        assert!(matches!(result, Err(SendError::BusClosed)));
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeHandle — 并发 (1 test)
    // ═══════════════════════════════════════════════════════════════

    // [并发] 多个节点同时 connect + send，消息全部送达
    #[tokio::test]
    async fn concurrent_connect_and_send_no_lost_messages() {
        let bus = Arc::new(test_bus());
        let mut rx = bus.subscribe();
        let handles: Vec<_> = (0..5)
            .map(|i| {
                let bus = bus.clone();
                tokio::spawn(async move {
                    let info = test_node_info(&format!("node-{i}"));
                    let h = bus.connect(info, test_filter()).await.unwrap();
                    h.send("ping", vec![], serde_json::json!(i)).await.unwrap();
                    h
                })
            })
            .collect();
        let mut node_handles = Vec::new();
        for h in handles {
            node_handles.push(h.await.unwrap());
        }
        let mut ping_count = 0;
        let mut online_count = 0;
        for _ in 0..10 {
            let msg = rx.recv().await.unwrap();
            match msg.msg_type.as_str() {
                "node_online" => online_count += 1,
                "ping" => ping_count += 1,
                _ => {}
            }
        }
        assert_eq!(online_count, 5);
        assert_eq!(ping_count, 5);
        for h in node_handles {
            h.disconnect().await;
        }
        let bus = Arc::into_inner(bus).unwrap();
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // recv — filter 端到端 (1 test)
    // ═══════════════════════════════════════════════════════════════

    // [过滤] recv() 只返回通过 filter 的消息
    #[tokio::test]
    async fn recv_respects_message_filter() {
        let bus = test_bus();
        let filter = MessageFilter {
            types: Some(vec!["action".into()]),
            to_match: ToMatch::BroadcastOnly,
        };
        let mut receiver = bus.connect(test_node_info("r"), filter).await.unwrap();
        let sender = bus
            .connect(test_node_info("s"), test_filter())
            .await
            .unwrap();
        sender
            .send("noise", vec![], serde_json::json!(null))
            .await
            .unwrap();
        sender
            .send(
                "action",
                vec![NodeId::new("r")],
                serde_json::json!("directed"),
            )
            .await
            .unwrap();
        sender
            .send("action", vec![], serde_json::json!("run"))
            .await
            .unwrap();
        let msg = receiver.recv().await.unwrap();
        assert_eq!(msg.msg_type, "action");
        assert_eq!(msg.payload, serde_json::json!("run"));
        receiver.disconnect().await;
        sender.disconnect().await;
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // Multi-Bus (7 tests)
    // ═══════════════════════════════════════════════════════════════

    // [多Bus] attach_to 后 subscriptions 列表包含两条 BusId
    #[tokio::test]
    async fn attach_to_adds_subscription() {
        let bus_a = Arc::new(test_bus());
        let bus_b = Arc::new(test_bus());
        let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
        let bid_b = handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();
        let subs = handle.subscriptions();
        assert_eq!(subs.len(), 2);
        assert_eq!(subs[0], bus_a.id);
        assert_eq!(subs[1], bid_b);
        assert_eq!(bid_b, bus_b.id);
        handle.disconnect().await;
        bus_a.signal_shutdown();
        bus_b.signal_shutdown();
    }

    // [多Bus] 在 bus_a 上 send，在 bus_b 上 send，两条消息都能从 handle.recv() 读到
    #[tokio::test]
    async fn multi_bus_recv_from_both() {
        let bus_a = Arc::new(test_bus());
        let bus_b = Arc::new(test_bus());
        let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
        handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();

        let sender_a = bus_a.connect(test_node_info("sa"), test_filter()).await.unwrap();
        let sender_b = bus_b.connect(test_node_info("sb"), test_filter()).await.unwrap();

        sender_a.send("from_a", vec![], serde_json::json!(1)).await.unwrap();
        sender_b.send("from_b", vec![], serde_json::json!(2)).await.unwrap();

        let mut got_a = false;
        let mut got_b = false;
        for _ in 0..8 {
            let msg = handle.recv().await.unwrap();
            match msg.msg_type.as_str() {
                "from_a" => got_a = true,
                "from_b" => got_b = true,
                _ => {} // drain node_online etc.
            }
            if got_a && got_b {
                break;
            }
        }
        assert!(got_a && got_b);

        sender_a.disconnect().await;
        sender_b.disconnect().await;
        handle.disconnect().await;
        bus_a.signal_shutdown();
        bus_b.signal_shutdown();
    }

    // [多Bus] per-Bus filter 隔离
    #[tokio::test]
    async fn multi_bus_filters_isolated_per_subscription() {
        let bus_a = Arc::new(test_bus());
        let bus_b = Arc::new(test_bus());
        let filter_a = MessageFilter {
            types: Some(vec!["a_only".into()]),
            to_match: ToMatch::All,
        };
        let filter_b = MessageFilter {
            types: Some(vec!["b_only".into()]),
            to_match: ToMatch::All,
        };
        let mut handle = bus_a.connect(test_node_info("multi"), filter_a).await.unwrap();
        handle.attach_to(bus_b.clone(), filter_b).await.unwrap();

        let sender_a = bus_a.connect(test_node_info("sa"), test_filter()).await.unwrap();
        let sender_b = bus_b.connect(test_node_info("sb"), test_filter()).await.unwrap();
        sender_a.send("a_only", vec![], serde_json::json!(1)).await.unwrap();
        sender_b.send("b_only", vec![], serde_json::json!(2)).await.unwrap();
        sender_a.send("b_only", vec![], serde_json::json!(3)).await.unwrap();
        sender_b.send("a_only", vec![], serde_json::json!(4)).await.unwrap();

        let mut got_a = false;
        let mut got_b = false;
        for _ in 0..2 {
            let msg = handle.recv().await.unwrap();
            match msg.msg_type.as_str() {
                "a_only" => got_a = true,
                "b_only" => got_b = true,
                _ => panic!("unexpected msg_type: {}", msg.msg_type),
            }
        }
        assert!(got_a && got_b);

        sender_a.disconnect().await;
        sender_b.disconnect().await;
        handle.disconnect().await;
        bus_a.signal_shutdown();
        bus_b.signal_shutdown();
    }

    // [多Bus] send_via 选 BusId 路由
    #[tokio::test]
    async fn send_via_routes_to_specific_bus() {
        let bus_a = Arc::new(test_bus());
        let bus_b = Arc::new(test_bus());
        let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
        handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();

        let mut rx_b = bus_b.subscribe();
        // Drain handle's node_online on bus_b
        let _ = rx_b.recv().await.unwrap();

        handle
            .send_via(bus_b.id, "on_b", vec![], serde_json::json!("hello_b"))
            .await
            .unwrap();

        let msg_b = rx_b.recv().await.unwrap();
        assert_eq!(msg_b.msg_type, "on_b");

        handle.disconnect().await;
        bus_a.signal_shutdown();
        bus_b.signal_shutdown();
    }

    // [多Bus] send_via 用未知 BusId → SendError::NoSuchBus
    #[tokio::test]
    async fn send_via_unknown_bus_returns_error() {
        let bus_a = Arc::new(test_bus());
        let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
        let bogus = BusId::new();
        let result = handle
            .send_via(bogus, "x", vec![], serde_json::json!(null))
            .await;
        assert!(matches!(result, Err(SendError::NoSuchBus(_))));
        handle.disconnect().await;
        bus_a.signal_shutdown();
    }

    // [多Bus] disconnect 触发两条 Bus 的 node_offline
    #[tokio::test]
    async fn disconnect_broadcasts_offline_on_all_buses() {
        let bus_a = Arc::new(test_bus());
        let bus_b = Arc::new(test_bus());
        let mut rx_a = bus_a.subscribe();
        let mut rx_b = bus_b.subscribe();
        let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
        handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();

        // Drain all node_online events (2 each: handle primary + handle attach_to)
        for _ in 0..4 {
            let _ = tokio::select! {
                m = rx_a.recv() => m.unwrap(),
                m = rx_b.recv() => m.unwrap(),
            };
        }

        handle.disconnect().await;

        // Each Bus should broadcast node_offline for "multi"
        let mut saw_offline = 0;
        for _ in 0..2 {
            let msg = tokio::select! {
                m = rx_a.recv() => m.unwrap(),
                m = rx_b.recv() => m.unwrap(),
            };
            if msg.msg_type == "node_offline" && msg.from.as_str() == "multi" {
                saw_offline += 1;
            }
        }
        assert_eq!(saw_offline, 2);

        bus_a.signal_shutdown();
        bus_b.signal_shutdown();
    }

    // [多Bus] shutdown 一条 Bus 后 handle 仍能 recv 另一条 Bus 的消息
    #[tokio::test]
    async fn shutdown_one_bus_keeps_handle_alive() {
        let bus_a = Arc::new(test_bus());
        let bus_b = Arc::new(test_bus());
        let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
        handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();

        let sender_b = bus_b.connect(test_node_info("sb"), test_filter()).await.unwrap();

        bus_a.signal_shutdown();

        sender_b.send("alive", vec![], serde_json::json!("yes")).await.unwrap();
        // Drain node_online events; assert "alive" eventually arrives.
        let mut got_alive = false;
        for _ in 0..6 {
            let msg = tokio::time::timeout(Duration::from_secs(2), handle.recv())
                .await
                .expect("recv should complete within 2s")
                .unwrap();
            if msg.msg_type == "alive" {
                got_alive = true;
                break;
            }
        }
        assert!(got_alive, "expected 'alive' message from bus_b after bus_a shutdown");

        sender_b.disconnect().await;
        handle.disconnect().await;
        bus_b.signal_shutdown();
    }
}