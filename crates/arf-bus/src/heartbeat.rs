//! Heartbeat detection — timer-driven liveness checks.
//!
//! The Bus periodically broadcasts `heartbeat_request` messages.
//! Each `NodeHandle` automatically acks when it consumes one.
//! Nodes that don't ack within `heartbeat_timeout` are marked offline.

use arf_core::{Message, NodeId, NodeInfo};
use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};
use tokio::sync::broadcast;

use crate::NodeEntry;

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

    // 2. Collect timed-out nodes (read lock, no mutation)
    let timed_out: Vec<(NodeId, NodeInfo)> = {
        let map = nodes.read().unwrap();
        map.iter()
            .filter(|(_, entry)| now.duration_since(entry.last_ack) > heartbeat_timeout)
            .map(|(id, entry)| (id.clone(), entry.info.clone()))
            .collect()
    };

    // 3. Remove and broadcast node_offline for each (write lock per removal)
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

// ═══════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use crate::{Bus, BusCommand};
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
            Duration::from_millis(5),  // fast heartbeat for test
            Duration::from_secs(10),   // long timeout
            16,
        );
        let mut handle = bus
            .connect(test_node_info("n"), test_filter())
            .await
            .unwrap();

        // Wait for heartbeat_request to be broadcast and consumed
        tokio::time::sleep(Duration::from_millis(30)).await;

        // try_recv should not return heartbeat_request (it filters them)
        while let Ok(Some(msg)) = handle.try_recv() {
            assert_ne!(
                msg.msg_type, "heartbeat_request",
                "heartbeat_request should be filtered by recv/try_recv"
            );
        }

        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [心跳] NodeHandle.try_recv() 过滤 heartbeat 并自动 ACK
    #[tokio::test]
    async fn try_recv_filters_heartbeat_and_acks() {
        let bus = Bus::new(
            Duration::from_millis(5),
            Duration::from_secs(10),
            16,
        );
        let mut handle = bus
            .connect(test_node_info("n"), test_filter())
            .await
            .unwrap();

        // Wait for at least one heartbeat
        tokio::time::sleep(Duration::from_millis(20)).await;

        // Call try_recv() — should return None (no app messages) but internally
        // it filtered heartbeats and sent acks. The node should still be alive.
        let result = handle.try_recv().unwrap();
        assert!(result.is_none(), "no app messages, heartbeats filtered");

        // Node should still be connected (not timed out)
        // We verify by sending a message successfully
        let receipt = handle
            .send("still_alive", None, serde_json::json!(null))
            .await
            .unwrap();
        assert_eq!(receipt.online_nodes, 1);

        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [心跳] heartbeat_request 被 raw subscribe() 可见（CAN 模型）
    #[tokio::test]
    async fn heartbeat_request_visible_on_raw_subscribe() {
        let bus = Bus::new(
            Duration::from_millis(10),
            Duration::from_secs(10),
            16,
        );
        let mut rx = bus.subscribe();

        // Raw subscriber sees heartbeat_request (after first tick is consumed)
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
    async fn node_without_ack_times_out() {
        let bus = Bus::new(
            Duration::from_millis(20),   // heartbeat interval
            Duration::from_millis(40),   // short timeout for test
            16,
        );
        let mut rx = bus.subscribe();

        // Connect a node that NEVER calls recv — won't send HeartbeatAck
        let handle = bus
            .connect(test_node_info("zombie"), test_filter())
            .await
            .unwrap();
        // Drain node_online
        let _ = rx.recv().await.unwrap();

        // Wait for timeout (need several ticks to pass the timeout)
        tokio::time::sleep(Duration::from_millis(150)).await;

        // Drain heartbeat_request messages, look for node_offline
        let mut saw_offline = false;
        for _ in 0..20 {
            match tokio::time::timeout(Duration::from_millis(30), rx.recv()).await {
                Ok(Ok(msg)) => {
                    if msg.msg_type == "node_offline" && msg.from.as_str() == "zombie" {
                        saw_offline = true;
                        break;
                    }
                }
                _ => break, // timeout or closed
            }
        }
        assert!(saw_offline, "zombie node should have been marked offline");

        // Clean up disconnect will silently fail (node already removed)
        handle.disconnect().await;
        bus.shutdown().await;
    }

    // [超时] 手动发 HeartbeatAck → last_ack 更新 → 不超时
    #[tokio::test]
    async fn heartbeat_ack_prevents_timeout() {
        let bus = Bus::new(
            Duration::from_millis(20),
            Duration::from_millis(100),   // longer timeout to avoid race
            16,
        );
        let mut rx = bus.subscribe();
        let handle = bus
            .connect(test_node_info("acked"), test_filter())
            .await
            .unwrap();
        let _ = rx.recv().await.unwrap(); // drain node_online

        // Send HeartbeatAck early (before any tick could time us out)
        tokio::time::sleep(Duration::from_millis(10)).await;
        bus.cmd_tx
            .send(BusCommand::HeartbeatAck {
                node_id: NodeId::new("acked"),
            })
            .await
            .unwrap();

        // Send another ack halfway through to ensure we stay alive
        tokio::time::sleep(Duration::from_millis(50)).await;
        bus.cmd_tx
            .send(BusCommand::HeartbeatAck {
                node_id: NodeId::new("acked"),
            })
            .await
            .unwrap();

        // Drain all messages, check no node_offline for "acked"
        tokio::time::sleep(Duration::from_millis(10)).await;
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

    // ═══════════════════════════════════════════════════════════════
    // 边界 (2 tests)
    // ═══════════════════════════════════════════════════════════════

    // [边界] heartbeat_interval=0 不 panic（立即 tick）
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

    // [边界] shutdown 期间 heartbeat tick 不 panic
    #[tokio::test]
    async fn heartbeat_shutdown_no_panic() {
        let bus = Bus::new(
            Duration::from_millis(10),
            Duration::from_secs(10),
            16,
        );
        // Connect a node and shutdown immediately
        let handle = bus
            .connect(test_node_info("n"), test_filter())
            .await
            .unwrap();
        handle.disconnect().await;

        // Shutdown should complete cleanly even with heartbeat timer running
        tokio::time::timeout(Duration::from_secs(1), bus.shutdown())
            .await
            .expect("shutdown should complete quickly");
    }
}

