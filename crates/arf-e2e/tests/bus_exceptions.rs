//! bus_exceptions.rs — Phase 9 task 9.1.5
//!
//! 探查 Bus 容错/异常路径，收尾 9.1 A 总线基线大类。
//! 子场景 1：Lagged CAN-bus sender-never-blocks。
//! 子场景 2：drop handle 掉线 → heartbeat timeout 剔除 + node_offline。
//! 子场景 3：disconnect/reconnect + 重复 connect AlreadyConnected。
//! 不引 Engine / ModelAdapter / McpNode。
//! 输出物是 audit-probe-9.1.5.md（独立文件，独立 commit）。

use std::sync::Arc;
use std::time::Duration;

use arf_bus::{Bus, ConnectError};
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use serde_json::json;

fn info(id: &str) -> NodeInfo {
    NodeInfo { node_id: NodeId::new(id), node_type: "node".into(), capabilities: json!({}), online_since: 0 }
}

fn all_filter() -> MessageFilter {
    MessageFilter { types: None, to_match: ToMatch::All }
}

fn online_ids(bus: &Bus) -> Vec<String> {
    bus.graph().nodes.iter().map(|n| n.node_id.as_str().to_string()).collect()
}

/// 子场景 1：Lagged — sender 猛发 ≫ capacity，慢 consumer 不 recv，sender 永不 block/err。
#[tokio::test]
async fn lagged_sender_never_blocks() {
    let bus = Arc::new(Bus::new(Duration::from_millis(200), Duration::from_secs(2), 8));
    let sender = bus.connect(info("sender"), all_filter()).await.expect("sender connect");
    let _slow = bus.connect(info("slow"), all_filter()).await.expect("slow connect"); // 主动不 recv
    tokio::time::sleep(Duration::from_millis(300)).await;

    // 连发 50 条 ≫ capacity 8
    for i in 0..50 {
        let r = sender.send("spam", vec![], json!({ "i": i })).await;
        assert!(r.is_ok(), "send {i} should not block/err, got {r:?}");
    }
    // bus 存活：sender + slow 仍在线
    let ids = online_ids(&bus);
    assert!(ids.contains(&"sender".to_string()) && ids.contains(&"slow".to_string()), "bus should survive lag: {ids:?}");
    println!("lagged: 50 sends all Ok, online={ids:?}");
}

/// 子场景 2：掉线 — drop handle 后 heartbeat timeout 剔除 ghost，observer 持续 ack 保持在线。
#[tokio::test]
async fn ghost_offline_after_timeout() {
    let bus = Arc::new(Bus::new(Duration::from_millis(200), Duration::from_millis(500), 32));
    let _observer = bus.connect(info("observer"), all_filter()).await.expect("observer connect");
    {
        let _ghost = bus.connect(info("ghost"), all_filter()).await.expect("ghost connect");
        tokio::time::sleep(Duration::from_millis(150)).await;
        assert!(online_ids(&bus).contains(&"ghost".to_string()), "ghost should be online before drop");
    } // drop ghost handle — forward task 停 ack

    tokio::time::sleep(Duration::from_millis(1200)).await; // 超 timeout(500ms) + 数个 tick
    let ids = online_ids(&bus);
    assert!(!ids.contains(&"ghost".to_string()), "ghost should be evicted after timeout: {ids:?}");
    assert!(ids.contains(&"observer".to_string()), "observer should stay online: {ids:?}");
    println!("offline: after timeout online={ids:?}");
}

/// 子场景 3：重连 — disconnect 后同 NodeId 可 reconnect；未 disconnect 重复 connect → AlreadyConnected。
#[tokio::test]
async fn reconnect_and_already_connected() {
    let bus = Arc::new(Bus::new(Duration::from_millis(200), Duration::from_secs(2), 32));

    // 重连成功
    let h1 = bus.connect(info("reconn"), all_filter()).await.expect("first connect");
    h1.disconnect().await;
    tokio::time::sleep(Duration::from_millis(150)).await; // 等 Disconnect 从 nodes map 移除
    let h2 = bus.connect(info("reconn"), all_filter()).await;
    assert!(h2.is_ok(), "reconnect after disconnect should succeed, got {:?}", h2.err());

    // 未 disconnect 重复 connect → AlreadyConnected
    // 注：NodeHandle 未实现 Debug，无法直接 {dup:?}；先 match 再断言。
    let dup = bus.connect(info("reconn"), all_filter()).await;
    let is_already = matches!(dup, Err(ConnectError::AlreadyConnected(_)));
    assert!(is_already, "dup connect (no prior disconnect) should fail with AlreadyConnected");
    println!("reconnect: reconnect Ok, dup connect -> AlreadyConnected");
}
