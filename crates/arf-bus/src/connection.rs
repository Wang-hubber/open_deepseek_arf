//! Node connection lifecycle — connect, send, receive, disconnect.
//!
//! `NodeHandle` is the primary API that nodes use to interact with the Bus.

use arf_core::{Message, MessageFilter, NodeId, NodeInfo, SendError, SendReceipt};
use tokio::sync::{broadcast, mpsc, oneshot};

use crate::{Bus, BusCommand, ConnectError};

// ═══════════════════════════════════════════════════════════════════
// NodeHandle
// ═══════════════════════════════════════════════════════════════════

/// A connected node's handle to the Bus.
///
/// Created by `Bus::connect()`, consumed by `disconnect()`.
/// Used to send messages, receive messages, and query node info.
///
/// `recv()` requires `&mut self` because `broadcast::Receiver::recv()`
/// modifies internal cursor state. In async contexts where multiple
/// tasks need to receive, wrap in `Arc<Mutex<NodeHandle>>`.
pub struct NodeHandle {
    /// Clone of Bus.cmd_tx — sends commands to the message loop.
    pub(crate) cmd_tx: mpsc::Sender<BusCommand>,
    /// Subscription to the broadcast channel — receives all messages.
    pub(crate) broadcast_rx: broadcast::Receiver<Message>,
    /// This node's identity and capabilities.
    pub(crate) info: NodeInfo,
    /// Message filter for receive-side filtering (used in task 1.6).
    pub(crate) filter: MessageFilter,
}

impl NodeHandle {
    /// Send a message from this node.
    ///
    /// The `from` field is automatically filled from this node's `NodeInfo`.
    /// Returns a `SendReceipt` with online node counts.
    pub async fn send(
        &self,
        msg_type: &str,
        to: Option<NodeId>,
        payload: serde_json::Value,
    ) -> Result<SendReceipt, SendError> {
        let msg = Message::new(msg_type, self.info.node_id.clone(), to, payload);
        self.send_raw(msg).await
    }

    /// Send a pre-constructed Message.
    ///
    /// The `from` field is NOT overridden — use with caution.
    /// Prefer `send()` for normal use.
    async fn send_raw(&self, msg: Message) -> Result<SendReceipt, SendError> {
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

    /// Receive the next application-visible message from the Bus.
    ///
    /// Heartbeat requests are intercepted and auto-acknowledged — they are
    /// never returned to the caller. Blocks until a non-heartbeat message
    /// arrives.
    ///
    /// MessageFilter filtering will be applied in task 1.6.
    pub async fn recv(&mut self) -> Result<Message, broadcast::error::RecvError> {
        loop {
            let msg = self.broadcast_rx.recv().await?;
            if msg.msg_type == "heartbeat_request" {
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

    /// Try to receive a message without blocking.
    ///
    /// Heartbeat requests are intercepted and auto-acknowledged (using
    /// `try_send` since this is a synchronous method).
    /// Returns `Ok(Some(msg))` if a non-heartbeat message is available,
    /// `Ok(None)` if no message is ready.
    pub fn try_recv(&mut self) -> Result<Option<Message>, broadcast::error::TryRecvError> {
        loop {
            match self.broadcast_rx.try_recv() {
                Ok(msg) => {
                    if msg.msg_type == "heartbeat_request" {
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

    /// Get this node's identity and capabilities.
    pub fn node_info(&self) -> &NodeInfo {
        &self.info
    }

    /// Get this node's filter configuration.
    pub fn filter_config(&self) -> &MessageFilter {
        &self.filter
    }

    /// Disconnect this node from the Bus.
    ///
    /// Sends a Disconnect command to the message loop,
    /// which removes the NodeEntry and broadcasts `node_offline`.
    /// Consumes `self` — the handle is no longer usable after this.
    pub async fn disconnect(self) {
        let (tx, rx) = oneshot::channel();
        let _ = self
            .cmd_tx
            .send(BusCommand::Disconnect {
                node_id: self.info.node_id.clone(),
                respond_to: tx,
            })
            .await;
        let _ = rx.await;
    }
}

// ═══════════════════════════════════════════════════════════════════
// Bus — connect
// ═══════════════════════════════════════════════════════════════════

impl Bus {
    /// Connect a node to the Bus.
    ///
    /// Registers the node in the online nodes map, broadcasts `node_online`,
    /// and returns a `NodeHandle` for the node to use.
    ///
    /// The returned `NodeHandle`'s `broadcast_rx` is created **after**
    /// registration, so the node does not see its own `node_online` message.
    ///
    /// Returns `Err(ConnectError::AlreadyConnected)` if a node with the same
    /// `NodeId` is already online.
    pub async fn connect(
        &self,
        info: NodeInfo,
        filter: MessageFilter,
    ) -> Result<NodeHandle, ConnectError> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx
            .send(BusCommand::Connect {
                info: info.clone(),
                filter: filter.clone(),
                respond_to: tx,
            })
            .await
            .map_err(|_| ConnectError::BusClosed)?;

        // Wait for registration confirmation
        rx.await.map_err(|_| ConnectError::BusClosed)??;

        // Create the broadcast receiver AFTER registration,
        // so the node doesn't see its own node_online message.
        let broadcast_rx = self.broadcast_tx.subscribe();

        Ok(NodeHandle {
            cmd_tx: self.cmd_tx.clone(),
            broadcast_rx,
            info,
            filter,
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
        Bus::new(
            Duration::from_secs(1),
            Duration::from_secs(3),
            16,
        )
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeHandle — 构造 & 基本属性 (4 tests)
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

        // Subscribe BEFORE connect to capture node_online
        let mut rx = bus.subscribe();

        let info = test_node_info("node-a");
        let handle = bus.connect(info, test_filter()).await.unwrap();

        // rx should receive node_online
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.msg_type, "node_online");
        assert_eq!(msg.from.as_str(), "node-a");

        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [构造] filter_config() 返回传入的 MessageFilter
    #[tokio::test]
    async fn node_handle_stores_filter_config() {
        let bus = test_bus();
        let filter = MessageFilter {
            types: Some(vec!["model_call".into()]),
            to_match: ToMatch::DirectedToMe,
        };
        let handle = bus.connect(test_node_info("n1"), filter.clone()).await.unwrap();
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

        // Drain node_online (sent during connect)
        let _ = rx.recv().await.unwrap();

        let receipt = sender
            .send("action", None, serde_json::json!({"cmd": "run"}))
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

        // Only sender is online (plus the sender itself = 1)
        let receipt = sender
            .send("t", None, serde_json::json!(null))
            .await
            .unwrap();
        assert_eq!(receipt.online_nodes, 1);

        // Connect another node
        let other = bus
            .connect(test_node_info("o"), test_filter())
            .await
            .unwrap();
        let receipt2 = sender
            .send("t", None, serde_json::json!(null))
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
            .send("ping", None, serde_json::json!("hello"))
            .await
            .unwrap();

        let msg = receiver.recv().await.unwrap();
        // Receiver may see sender's node_online first
        if msg.msg_type == "node_online" {
            let msg2 = receiver.recv().await.unwrap();
            assert_eq!(msg2.msg_type, "ping");
            assert_eq!(msg2.payload, serde_json::json!("hello"));
        } else {
            assert_eq!(msg.msg_type, "ping");
            assert_eq!(msg.payload, serde_json::json!("hello"));
        }

        receiver.disconnect().await;
        sender.disconnect().await;
        bus.shutdown().await;
    }

    // [数据] try_recv() 无消息时返回 Ok(None)
    #[tokio::test]
    async fn try_recv_returns_none_when_empty() {
        let bus = test_bus();
        let mut handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();

        // Should be empty initially (broadcast_rx created after node_online)
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

        // Drain sender's node_online
        let _ = receiver.recv().await.unwrap();

        for i in 0..5 {
            sender
                .send("seq", None, serde_json::json!(i))
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

        // Drain node_online
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

        // Reconnect with the same NodeId
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

        // Drain node_online + node_offline
        let _ = rx.recv().await.unwrap();
        let _ = rx.recv().await.unwrap();

        let h2 = bus.connect(info, test_filter()).await.unwrap();
        h2.send("after_reconnect", None, serde_json::json!("ok"))
            .await
            .unwrap();

        // Drain node_online then verify our message
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
        let mut a = bus.connect(test_node_info("A"), test_filter()).await.unwrap();
        // A's rx created after A's node_online, so A doesn't see its own
        let mut b = bus.connect(test_node_info("B"), test_filter()).await.unwrap();
        // B's rx created after both node_online, so B sees nothing from history

        // Only A sees B's node_online (A's rx was created before B connected)
        let msg = a.recv().await.unwrap();
        assert_eq!(msg.msg_type, "node_online");
        assert_eq!(msg.from.as_str(), "B");

        a.send("chat", None, serde_json::json!("from_a"))
            .await
            .unwrap();
        b.send("chat", None, serde_json::json!("from_b"))
            .await
            .unwrap();

        // Each node sees both messages (broadcast includes self).
        // Drain self-echo, verify the peer's message.
        let a_first = a.recv().await.unwrap(); // A's own "from_a" (self-echo)
        let a_second = a.recv().await.unwrap(); // B's "from_b"
        assert_eq!(a_first.payload, serde_json::json!("from_a"));
        assert_eq!(a_second.payload, serde_json::json!("from_b"));

        let b_first = b.recv().await.unwrap(); // A's "from_a"
        let b_second = b.recv().await.unwrap(); // B's own "from_b" (self-echo)
        assert_eq!(b_first.payload, serde_json::json!("from_a"));
        assert_eq!(b_second.payload, serde_json::json!("from_b"));

        a.disconnect().await;
        b.disconnect().await;
        bus.shutdown().await;
    }

    // [多节点] 新节点看不到 connect 之前的历史消息（subscribe 在连接后才创建）
    #[tokio::test]
    async fn late_joiner_sees_only_future_messages() {
        let bus = test_bus();
        let early = bus
            .connect(test_node_info("early"), test_filter())
            .await
            .unwrap();

        // Send before late joiner connects
        early
            .send("historical", None, serde_json::json!("ancient"))
            .await
            .unwrap();

        let mut late = bus
            .connect(test_node_info("late"), test_filter())
            .await
            .unwrap();

        // late's rx was created AFTER both early's node_online AND "historical".
        // So late sees nothing from the past. Send a new message, late should see it.
        early
            .send("current", None, serde_json::json!("now"))
            .await
            .unwrap();
        let msg = late.recv().await.unwrap();
        assert_eq!(msg.msg_type, "current");
        assert_eq!(msg.payload, serde_json::json!("now"));

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

        // Survivor can still send
        let receipt = survivor
            .send("still_here", None, serde_json::json!("ok"))
            .await
            .unwrap();
        assert_eq!(receipt.online_nodes, 1); // only survivor remains

        survivor.disconnect().await;
        bus.shutdown().await;
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeHandle — shutdown 交互 (2 tests)
    // ═══════════════════════════════════════════════════════════════

    // [关闭] shutdown 后 receiver 返回 Closed——Bus 消费后 broadcast channel 关闭
    #[tokio::test]
    async fn node_handle_recv_returns_closed_after_shutdown() {
        let bus = test_bus();
        let mut handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();

        bus.shutdown().await;

        let result = handle.recv().await;
        assert!(matches!(result, Err(broadcast::error::RecvError::Closed)));
    }

    // [关闭] shutdown 后 NodeHandle.send() 返回 BusClosed
    #[tokio::test]
    async fn node_handle_send_after_shutdown_returns_bus_closed() {
        let bus = test_bus();
        let handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();

        // Don't disconnect — just shutdown the bus
        bus.shutdown().await;

        let result = handle.send("t", None, serde_json::json!(null)).await;
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
                    h.send("ping", None, serde_json::json!(i)).await.unwrap();
                    h
                })
            })
            .collect();

        let mut node_handles = Vec::new();
        for h in handles {
            node_handles.push(h.await.unwrap());
        }

        // Count messages — 5 node_online + 5 ping = 10
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
}
