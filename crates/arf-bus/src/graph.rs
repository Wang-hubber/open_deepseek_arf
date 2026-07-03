//! Bus health graph — snapshot of online nodes and bus metrics.

use arf_core::BusGraph;

use crate::Bus;

impl Bus {
    /// Snapshot of the bus health at query time.
    ///
    /// Returns a `BusGraph` with the list of online nodes,
    /// total message count, and uptime in milliseconds.
    pub fn graph(&self) -> BusGraph {
        let map = self.nodes.read().unwrap();
        // F-008: sort by node_id so iteration order is deterministic — the
        // underlying `nodes` is a HashMap and HashMap iteration order is
        // randomised across processes. Without this sort, `resolve_model`
        // (and other consumers that scan the graph) could pick a different
        // node on different runs.
        let mut nodes: Vec<_> = map.values().map(|entry| entry.info.clone()).collect();
        nodes.sort_by(|a, b| a.node_id.as_str().cmp(b.node_id.as_str()));

        BusGraph {
            nodes,
            message_count: self.message_count(),
            uptime_ms: self.uptime_ms(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
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

    fn test_bus() -> Bus {
        Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16)
    }

    // [构造] 空 Bus → graph 返回空节点列表
    #[tokio::test]
    async fn graph_empty_bus() {
        let bus = test_bus();
        let g = bus.graph();
        assert!(g.nodes.is_empty());
        assert_eq!(g.message_count, 0);
        assert!(g.uptime_ms < 100);
        bus.shutdown().await;
    }

    // [构造] 有节点 → graph 包含所有 NodeInfo
    #[tokio::test]
    async fn graph_with_nodes() {
        let bus = test_bus();
        let h1 = bus
            .connect(test_node_info("engine/main"), test_filter())
            .await
            .unwrap();
        let h2 = bus
            .connect(test_node_info("mcp/filesystem"), test_filter())
            .await
            .unwrap();

        let g = bus.graph();
        assert_eq!(g.nodes.len(), 2);
        let ids: Vec<&str> = g.nodes.iter().map(|n| n.node_id.as_str()).collect();
        assert!(ids.contains(&"engine/main"));
        assert!(ids.contains(&"mcp/filesystem"));

        h1.disconnect().await;
        h2.disconnect().await;
        bus.shutdown().await;
    }

    // [数据] message_count 反映已发送消息数
    #[tokio::test]
    async fn graph_message_count_reflects_sends() {
        let bus = test_bus();
        let sender = bus
            .connect(test_node_info("s"), test_filter())
            .await
            .unwrap();

        let count_before = bus.graph().message_count;

        sender
            .send("t", vec![], serde_json::json!(null))
            .await
            .unwrap();
        sender
            .send("t", vec![], serde_json::json!(null))
            .await
            .unwrap();

        let g = bus.graph();
        assert_eq!(g.message_count, count_before + 2);

        sender.disconnect().await;
        bus.shutdown().await;
    }

    // [快照] graph 是快照，disconnect 后旧 graph 不变
    #[tokio::test]
    async fn graph_is_snapshot_does_not_update() {
        let bus = test_bus();
        let handle = bus
            .connect(test_node_info("ephemeral"), test_filter())
            .await
            .unwrap();

        let g1 = bus.graph();
        assert_eq!(g1.nodes.len(), 1);

        handle.disconnect().await;

        // g1 is a snapshot — still shows 1 node
        assert_eq!(g1.nodes.len(), 1);

        // New graph shows 0 nodes
        let g2 = bus.graph();
        assert_eq!(g2.nodes.len(), 0);

        bus.shutdown().await;
    }

    // [数据] C4 F-008: graph.nodes 迭代顺序按 node_id 排序（确定）
    #[tokio::test]
    async fn graph_node_iteration_is_deterministic() {
        let bus = test_bus();
        // Connect in a non-alphabetical order to expose any HashMap-iteration
        // order dependence.
        for id in ["z", "a", "m", "b"] {
            bus.connect(test_node_info(id), test_filter()).await.unwrap();
        }
        // Call graph() many times — must always produce the same sorted order.
        for _ in 0..20 {
            let g = bus.graph();
            let ids: Vec<&str> = g.nodes.iter().map(|n| n.node_id.as_str()).collect();
            assert_eq!(ids, vec!["a", "b", "m", "z"]);
        }
        bus.shutdown().await;
    }
}
