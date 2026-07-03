//! multi_bus_attach.rs — Phase 9 task 9.1.3
//!
//! 探查 Bus + multi-bus 拓扑（NodeHandle.attach_to）。
//! 一个 worker Node 跨 2 个 Bus：connect(bus_a) + attach_to(bus_b)，
//! per-subscription filter 各异（bus_a=[task_a] / bus_b=[task_b]）。
//! 不引 Engine / ModelAdapter / McpNode。
//! 输出物是 audit-probe-9.1.3.md（独立文件，独立 commit）。

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::{Bus, NodeHandle};
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use serde_json::json;

fn info(id: &str, node_type: &str) -> NodeInfo {
    NodeInfo { node_id: NodeId::new(id), node_type: node_type.into(), capabilities: json!({}), online_since: 0 }
}

fn types_filter(types: &[&str]) -> MessageFilter {
    MessageFilter { types: Some(types.iter().map(|s| s.to_string()).collect()), to_match: ToMatch::All }
}

fn drain_types(h: &mut NodeHandle) -> HashSet<String> {
    let mut out = HashSet::new();
    while let Ok(Some(msg)) = h.try_recv() {
        out.insert(msg.msg_type);
    }
    out
}

#[tokio::test]
async fn multi_bus_attach_graph_and_filter_isolation() {
    let bus_a = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let bus_b = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // worker: primary=bus_a(filter [task_a]) + attached bus_b(filter [task_b])
    let mut worker = bus_a.connect(info("worker/w1", "worker"), types_filter(&["task_a"])).await.expect("connect a");
    let bid_b = worker.attach_to(bus_b.clone(), types_filter(&["task_b"])).await.expect("attach b");
    assert_eq!(worker.primary_bus_id(), bus_a.id, "primary must be bus_a");
    assert_eq!(bid_b, bus_b.id, "attached id must be bus_b");

    // 各 Bus 一个 sender（types=None 全收，便于验证定向）
    let sender_a = bus_a.connect(info("sender/a", "sender"), MessageFilter { types: None, to_match: ToMatch::All }).await.expect("sa");
    let sender_b = bus_b.connect(info("sender/b", "sender"), MessageFilter { types: None, to_match: ToMatch::All }).await.expect("sb");
    tokio::time::sleep(Duration::from_millis(800)).await;

    // ── 探查点 1：双 Bus graph 独立，worker 同 NodeId 双现 ──
    let ga = bus_a.graph();
    let gb = bus_b.graph();
    let ids_a: HashSet<_> = ga.nodes.iter().map(|n| n.node_id.as_str().to_string()).collect();
    let ids_b: HashSet<_> = gb.nodes.iter().map(|n| n.node_id.as_str().to_string()).collect();
    assert_eq!(ids_a, HashSet::from(["worker/w1".to_string(), "sender/a".to_string()]), "bus_a graph: {ids_a:?}");
    assert_eq!(ids_b, HashSet::from(["worker/w1".to_string(), "sender/b".to_string()]), "bus_b graph: {ids_b:?}");

    // ── 探查点 2：per-subscription filter 跨 Bus 隔离 ──
    // sender_a broadcast task_a+task_b 到 bus_a；sender_b broadcast task_a+task_b 到 bus_b
    sender_a.send("task_a", vec![], json!({})).await.expect("sa task_a");
    sender_a.send("task_b", vec![], json!({})).await.expect("sa task_b");
    sender_b.send("task_a", vec![], json!({})).await.expect("sb task_a");
    sender_b.send("task_b", vec![], json!({})).await.expect("sb task_b");
    tokio::time::sleep(Duration::from_millis(200)).await;

    // worker: bus_a 只放 task_a、bus_b 只放 task_b → recv 汇聚得 {task_a, task_b}
    let got = drain_types(&mut worker);
    assert_eq!(got, HashSet::from(["task_a".to_string(), "task_b".to_string()]), "worker recv: {got:?}");

    // ── 探查点 3：send_via 定向不泄漏到另一 Bus ──
    let mut sa = sender_a;
    let mut sb = sender_b;
    let _ = drain_types(&mut sa);
    let _ = drain_types(&mut sb);
    worker.send_via(bid_b, "ping_b", vec![], json!({})).await.expect("ping_b via bus_b");
    tokio::time::sleep(Duration::from_millis(200)).await;
    let sa_got = drain_types(&mut sa);
    let sb_got = drain_types(&mut sb);
    assert!(!sa_got.contains("ping_b"), "ping_b leaked to bus_a: {sa_got:?}");
    assert!(sb_got.contains("ping_b"), "ping_b missing on bus_b: {sb_got:?}");

    println!("worker_got={got:?}");
    println!("sa_got={sa_got:?} sb_got={sb_got:?}");
}
