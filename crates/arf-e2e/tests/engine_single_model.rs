//! engine_single_model.rs — Phase 9 task 9.2.1
//!
//! 探查 Engine + 单 ModelAdapter（情景 §2.1 单 agent 无 tool）。
//! 首个引入 Engine 的探查：最小 chat（user → model_call → model_response → final）。
//! scripted mock provider，离线、确定性，不依赖任何 LLM。
//! 输出物是 audit-probe-9.2.1.md（独立文件，独立 commit）。

mod common;

use common::harness::{E2EHarness, ProviderKind};
use common::provider::simple_mock;

#[tokio::test]
async fn engine_single_model_minimal_chat() {
    // Engine + 单 ModelAdapter，mock 返回单条 text
    let mut h = E2EHarness::new(ProviderKind::Mock(simple_mock("hello from model")))
        .await
        .expect("harness build");

    // ── chat 能力：user → model_call → model_response → final ──
    let out = h.run_react("hi").await.expect("run");
    assert_eq!(out, "hello from model", "final output mismatch");
    h.assert_state_messages(2); // user + assistant
    assert!(h.state.messages[1].tool_calls.is_empty(), "single-round text: no tool_calls");

    // ── Engine 组装接触点可观察量 ──
    assert!(!h.engine.agent_id().as_str().is_empty(), "agent_id should be set");
    assert!(!h.engine.session_id().is_empty(), "session_id should be set");

    // ── bus graph：engine node + model adapter node 共存 ──
    let g = h.engine.primary_bus().graph();
    let node_types: Vec<String> = g.nodes.iter().map(|n| n.node_type.clone()).collect();
    assert!(
        g.nodes.len() >= 2,
        "expected engine + model adapter nodes, got {} ({:?})",
        g.nodes.len(),
        node_types
    );

    println!("agent_id={} session_id={}", h.engine.agent_id().as_str(), h.engine.session_id());
    println!("online node_types={node_types:?}");
    println!("final_output={out:?} messages={}", h.state.messages.len());
}
