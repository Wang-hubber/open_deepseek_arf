//! barrier_multi.rs — Phase 9 task 9.1.4
//!
//! 探查 Bus + barrier 多参与者同步原语。
//! 场景 A：3 participant 全 ack；场景 B：只 2/3 ack（部分，超时）。
//! 各 participant 后台 spawn acker：recv barrier_request → 解析 correlation_id → barrier_ack。
//! 不引 Engine / ModelAdapter / McpNode。
//! 输出物是 audit-probe-9.1.4.md（独立文件，独立 commit）。

use std::sync::Arc;
use std::time::Duration;

use arf_bus::{Bus, NodeHandle};
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use serde_json::json;
use uuid::Uuid;

fn info(id: &str) -> NodeInfo {
    NodeInfo { node_id: NodeId::new(id), node_type: "participant".into(), capabilities: json!({}), online_since: 0 }
}

/// filter：只收 barrier_request，隔离 node_online 噪声
fn barrier_filter() -> MessageFilter {
    MessageFilter { types: Some(vec!["barrier_request".into()]), to_match: ToMatch::All }
}

/// 后台 acker：收到 barrier_request 就按其 correlation_id 回 ack
fn spawn_acker(mut h: NodeHandle) {
    tokio::spawn(async move {
        loop {
            match h.recv().await {
                Ok(msg) if msg.msg_type == "barrier_request" => {
                    if let Some(cid) = msg
                        .payload
                        .get("correlation_id")
                        .and_then(|v| v.as_str())
                        .and_then(|s| Uuid::parse_str(s).ok())
                    {
                        let _ = h.barrier_ack(cid).await;
                    }
                }
                Ok(_) => continue,
                Err(_) => break,
            }
        }
    });
}

#[tokio::test]
async fn barrier_multi_full_and_partial() {
    // ── 场景 A：3 participant 全 ack ──
    let bus1 = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    for id in ["p/1", "p/2", "p/3"] {
        let h = bus1.connect(info(id), barrier_filter()).await.expect("connect A");
        spawn_acker(h);
    }
    tokio::time::sleep(Duration::from_millis(300)).await; // 等 acker forward task 就绪

    let ra = bus1
        .barrier(vec![NodeId::new("p/1"), NodeId::new("p/2"), NodeId::new("p/3")], Duration::from_secs(1))
        .await;
    assert_eq!(ra.acked.len(), 3, "expected 3 acked, got {:?}", ra.acked);
    assert!(ra.missing.is_empty(), "expected no missing, got {:?}", ra.missing);
    assert!(!ra.timed_out, "full-ack should not time out");

    // ── 场景 B：只 2/3 ack（q/3 静默）──
    let bus2 = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let q1 = bus2.connect(info("q/1"), barrier_filter()).await.expect("connect q1");
    let q2 = bus2.connect(info("q/2"), barrier_filter()).await.expect("connect q2");
    let _q3 = bus2.connect(info("q/3"), barrier_filter()).await.expect("connect q3"); // hold，不 ack
    spawn_acker(q1);
    spawn_acker(q2);
    tokio::time::sleep(Duration::from_millis(300)).await;

    let rb = bus2
        .barrier(vec![NodeId::new("q/1"), NodeId::new("q/2"), NodeId::new("q/3")], Duration::from_millis(300))
        .await;
    assert_eq!(rb.acked.len(), 2, "expected 2 acked, got {:?}", rb.acked);
    assert_eq!(rb.missing, vec![NodeId::new("q/3")], "expected missing [q/3], got {:?}", rb.missing);
    assert!(rb.timed_out, "partial-ack should time out");

    println!("A: acked={:?} missing={:?} timed_out={}", ra.acked, ra.missing, ra.timed_out);
    println!("B: acked={:?} missing={:?} timed_out={}", rb.acked, rb.missing, rb.timed_out);
}
