//! Task 4 — `Engine::reset_state()` + `Engine::run_once()` for ephemeral lifecycle.
//!
//! Adaptation (2026-07-05, per brief): Engine + State split — `State` lives with the
//! caller and is borrowed via `&mut State`. There is no `Engine::state` field.
//! `reset_state(&self, state: &mut State)` and `run_once(&self, state: &mut State, ...)`
//! match the existing `run(&mut self, &mut State, ...)` pattern.
//!
//! `run_once` does NOT auto-reset state (caller explicitly calls `reset_state` after).

use std::sync::Arc;

use arf_core::{NodeId, State};
use arf_engine::{AgentConfig, EngineBuilder, TaskInput};

/// Minimal valid bus with a `node_type == "model"` node so `EngineBuilder::build`
/// passes the ResourceRegistry model-resolution check. Mirrors the helper in
/// `test_ephemeral.rs` (Task 3) and `tests/integration.rs`.
async fn test_bus_with_model_node() -> Arc<arf_bus::Bus> {
    let bus = Arc::new(arf_bus::Bus::new(
        std::time::Duration::from_secs(1),
        std::time::Duration::from_secs(3),
        16,
    ));
    let _ = bus
        .connect(
            arf_core::NodeInfo {
                node_id: NodeId::new("model/mock"),
                node_type: "model".into(),
                capabilities: serde_json::json!({"provider": "mock", "kind": "model"}),
                online_since: 0,
            },
            arf_core::MessageFilter {
                types: None,
                to_match: arf_core::ToMatch::All,
            },
        )
        .await;
    bus
}

fn minimal_config() -> AgentConfig {
    AgentConfig {
        model: arf_agent::ModelDecl {
            provider: "mock".into(),
            model_name: "mock-v1".into(),
            ..Default::default()
        },
        ..AgentConfig::default()
    }
}

/// TDD Step 4.2 — `reset_state` on a freshly built ephemeral engine must succeed.
/// State has no conversation history; outbox tracking is a placeholder returning `[]`.
#[tokio::test]
async fn reset_state_on_empty_state_ok() {
    let bus = test_bus_with_model_node().await;

    let engine = EngineBuilder::new(vec![bus])
        .ephemeral(true)
        .build(minimal_config())
        .await
        .unwrap();

    let mut state = State::new();
    let result = engine.reset_state(&mut state);
    assert!(result.is_ok(), "reset_state on empty state must succeed");
    // After reset, messages and turn_count stay zero.
    assert_eq!(state.messages.len(), 0);
    assert_eq!(state.over_view.turn_count, 0);
}

/// TDD Step 4.6 — `run_once` runs a ReAct round and then `reset_state` clears
/// the state. Mirrors the brief's `run_once_resets_state_for_ephemeral` but
/// adapted to Engine + State split: `run_once` does NOT auto-reset; the test
/// verifies the lifecycle by calling `reset_state` explicitly after `run_once`.
///
/// We use a mock responder that emits a single text-only model_response, so
/// `run()` returns after one model turn and the test can observe final state.
#[tokio::test]
async fn run_once_then_reset_clears_state() {
    use std::time::Duration;
    use tokio_util::sync::CancellationToken;
    use uuid::Uuid;

    let bus = test_bus_with_model_node().await;

    // Mock responder that replies to the next model_call with a text-only response.
    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let bus_for_resp = bus.clone();
    let resp_h = tokio::spawn(async move {
        let mut rx = bus_for_resp.subscribe();
        let _ = ready_tx.send(());
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(10);
        while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match m {
                Ok(m) => m,
                Err(_) => break,
            };
            if m.msg_type != "model_call" {
                continue;
            }
            let cid = match m
                .payload
                .get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok())
            {
                Some(c) => c,
                None => continue,
            };
            let payload = serde_json::json!({
                "correlation_id": cid.to_string(),
                "message": {"content": "hi back", "tool_calls": []},
            });
            let resp = arf_core::Message::with_from_bus(
                "model_response",
                NodeId::new("model/mock"),
                vec![],
                payload,
                bus_for_resp.id,
            );
            let _ = bus_for_resp.send(resp).await;
            break;
        }
    });
    ready_rx.await.unwrap();
    tokio::task::yield_now().await;

    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .ephemeral(true)
        .build(minimal_config())
        .await
        .unwrap();

    let mut state = State::new();
    let cancel = CancellationToken::new();

    // Run one ReAct round (text-only → engine returns after 1 model turn).
    let result = tokio::time::timeout(
        Duration::from_secs(5),
        engine.run_once(
            &mut state,
            TaskInput {
                user_message: "hi".into(),
            },
            cancel,
        ),
    )
    .await
    .expect("run_once timed out")
    .expect("run_once should succeed");

    assert_eq!(result.turns_consumed, 1);
    assert_eq!(result.output.as_str(), Some("hi back"));

    // After run_once, state contains the conversation. run_once does NOT auto-reset.
    assert!(
        !state.messages.is_empty(),
        "state should retain conversation after run_once (no auto-reset)"
    );

    // Caller explicitly calls reset_state to clear for next reuse.
    engine
        .reset_state(&mut state)
        .expect("reset_state should succeed on empty outbox");
    assert_eq!(
        state.messages.len(),
        0,
        "reset_state must clear conversation history"
    );
    assert_eq!(
        state.over_view.turn_count, 0,
        "reset_state must reset turn_count"
    );

    resp_h.abort();
}