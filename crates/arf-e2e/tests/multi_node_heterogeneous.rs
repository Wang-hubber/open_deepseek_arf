//! multi_node_heterogeneous.rs — Phase 9 task 9.1.2
//!
//! 探查 Bus + 多 Node 异构（baseline+1）。
//! 3 个 node_type 各异的 Node 接同一 Bus，各带不同 MessageFilter。
//! 不引 Engine / ModelAdapter / McpNode。
//! 输出物是 audit-probe-9.1.2.md（独立文件，独立 commit）。

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::{Bus, NodeHandle};
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use serde_json::json;

fn info(id: &str, node_type: &str, caps: serde_json::Value) -> NodeInfo {
    NodeInfo {
        node_id: NodeId::new(id),
        node_type: node_type.into(),
        capabilities: caps,
        online_since: 0,
    }
}

/// Drain all currently-available messages from a handle, return their msg_types.
fn drain_types(h: &mut NodeHandle) -> Vec<String> {
    let mut out = Vec::new();
    while let Ok(Some(msg)) = h.try_recv() {
        out.push(msg.msg_type);
    }
    out
}

#[tokio::test]
async fn multi_node_heterogeneous_filter_isolation() {
    // 同 baseline_bus 参数：heartbeat 500ms / timeout 2s / capacity 32
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // engine node — 只收 model_call / tool_exec
    let engine_filter = MessageFilter {
        types: Some(vec!["model_call".into(), "tool_exec".into()]),
        to_match: ToMatch::All,
    };
    let mut engine = bus
        .connect(info("engine/main", "engine", json!({"sessions": ["sid-1"]})), engine_filter)
        .await
        .expect("engine connect");

    // mcp node — types=None，全收（含 node_online 等隐式消息）
    let mcp_filter = MessageFilter { types: None, to_match: ToMatch::All };
    let mut mcp = bus
        .connect(info("mcp/fs", "mcp", json!({"tools": ["read", "write"]})), mcp_filter)
        .await
        .expect("mcp connect");

    // model node — 只收 model_response / model_response_chunk
    let model_filter = MessageFilter {
        types: Some(vec!["model_response".into(), "model_response_chunk".into()]),
        to_match: ToMatch::All,
    };
    let mut model = bus
        .connect(info("model/primary", "model", json!({"models": ["deepseek-v4"]})), model_filter)
        .await
        .expect("model connect");

    // 等 online 稳定 + 1 heartbeat tick
    tokio::time::sleep(Duration::from_millis(800)).await;

    // ── 探查点 1：graph() 聚合看见 3 个异构 Node ──
    let g = bus.graph();
    assert_eq!(g.nodes.len(), 3, "expected 3 nodes online, got {}", g.nodes.len());
    let types: HashSet<_> = g.nodes.iter().map(|n| n.node_type.as_str()).collect();
    assert_eq!(
        types,
        HashSet::from(["engine", "mcp", "model"]),
        "node_type set mismatch: {types:?}"
    );

    // ── 探查点 2：filter 隔离 + self-delivery ──
    // engine broadcast model_call；model broadcast model_response（to=[] 广播）
    engine.send("model_call", vec![], json!({"prompt": "hi"})).await.expect("engine send");
    model.send("model_response", vec![], json!({"text": "ok"})).await.expect("model send");
    tokio::time::sleep(Duration::from_millis(200)).await;

    let engine_got = drain_types(&mut engine);
    let mcp_got = drain_types(&mut mcp);
    let model_got = drain_types(&mut model);

    // engine filter=[model_call,tool_exec]：收到自己发的 model_call（self-delivery），不收 model_response
    assert!(engine_got.contains(&"model_call".to_string()), "engine missing model_call: {engine_got:?}");
    assert!(!engine_got.contains(&"model_response".to_string()), "engine leaked model_response: {engine_got:?}");

    // mcp filter=None：两条都收（外加隐式 node_online 噪声，不精确 count）
    assert!(mcp_got.contains(&"model_call".to_string()), "mcp missing model_call: {mcp_got:?}");
    assert!(mcp_got.contains(&"model_response".to_string()), "mcp missing model_response: {mcp_got:?}");

    // model filter=[model_response,...]：收到自己发的 model_response，不收 model_call
    assert!(model_got.contains(&"model_response".to_string()), "model missing model_response: {model_got:?}");
    assert!(!model_got.contains(&"model_call".to_string()), "model leaked model_call: {model_got:?}");

    println!("engine_got={engine_got:?}");
    println!("mcp_got={mcp_got:?}");
    println!("model_got={model_got:?}");
}
