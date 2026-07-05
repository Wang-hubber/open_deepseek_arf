//! Task 5 — SubagentPool delegating + recycling ephemeral engines.
//!
//! Pools wrap `EngineBuilder::ephemeral(true)` instances with caller-owned
//! `State`. Each `delegate()` call pops/reuses a slot, runs one ReAct round,
//! and recycles the slot (engine + state) back to the pool.

use std::sync::Arc;
use std::time::Duration;

use arf_core::{NodeId, State};
use arf_engine::{config::AgentConfig, TaskInput};
use arf_subagent_pool::{OutboxStrategy, SubagentPool};
use uuid::Uuid;

/// Minimal valid bus with a `node_type == "model"` node so `EngineBuilder::build`
/// passes the ResourceRegistry model-resolution check. Mirrors the helper in
/// `arf-engine/tests/test_ephemeral.rs` and `arf-engine/tests/test_run_once.rs`.
async fn test_bus_with_model_node() -> Arc<arf_bus::Bus> {
    let bus = Arc::new(arf_bus::Bus::new(
        Duration::from_secs(1),
        Duration::from_secs(3),
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

fn minimal_config() -> Arc<AgentConfig> {
    Arc::new(AgentConfig {
        model: arf_agent::ModelDecl {
            provider: "mock".into(),
            model_name: "mock-v1".into(),
            ..Default::default()
        },
        ..AgentConfig::default()
    })
}

/// Spawn a mock responder that replies to every `model_call` with a
/// text-only response. Stops when `shutdown` is signalled.
fn spawn_responder(
    bus: Arc<arf_bus::Bus>,
    shutdown: tokio::sync::oneshot::Receiver<()>,
) -> tokio::task::JoinHandle<()> {
    let bus_for_resp = bus.clone();
    tokio::spawn(async move {
        let mut rx = bus_for_resp.subscribe();
        let mut shutdown_rx = shutdown;
        loop {
            tokio::select! {
                biased;
                _ = &mut shutdown_rx => break,
                msg = rx.recv() => {
                    let m = match msg {
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
                        "message": {"content": "ok", "tool_calls": []},
                    });
                    let resp = arf_core::Message::with_from_bus(
                        "model_response",
                        NodeId::new("model/mock"),
                        vec![],
                        payload,
                        bus_for_resp.id,
                    );
                    let _ = bus_for_resp.send(resp).await;
                }
            }
        }
    })
}

/// TDD Step 5.3 / 5.5 — Pool delegates two tasks and recycles both slots.
#[tokio::test]
async fn pool_delegates_task_and_recycles() {
    let bus = test_bus_with_model_node().await;

    let (sd_tx, sd_rx) = tokio::sync::oneshot::channel::<()>();
    let resp_h = spawn_responder(bus.clone(), sd_rx);

    let pool = SubagentPool::new(bus, minimal_config(), 2);

    let r1 = tokio::time::timeout(
        Duration::from_secs(5),
        pool.delegate(TaskInput {
            user_message: "a".into(),
        }),
    )
    .await
    .expect("delegate1 timed out")
    .expect("delegate1 should succeed");
    assert_eq!(r1.turns_consumed, 1);

    let r2 = tokio::time::timeout(
        Duration::from_secs(5),
        pool.delegate(TaskInput {
            user_message: "b".into(),
        }),
    )
    .await
    .expect("delegate2 timed out")
    .expect("delegate2 should succeed");
    assert_eq!(r2.turns_consumed, 1);

    assert_eq!(pool.metrics().total_delegations, 2);
    // Lazy provisioning: 2 sequential delegates reuse 1 engine (built on
    // first call) → 1 idle slot. To get `size` idle slots, we'd need
    // `size` concurrent delegates (semaphore admits up to `size`).
    assert_eq!(
        pool.available(),
        1,
        "engine from first delegate is recycled (lazy provision: 1 of 2 slots)"
    );
    // OutboxStrategy::default() is constructible.
    let _ = OutboxStrategy::default();

    // Shut down responder cleanly.
    let _ = sd_tx.send(());
    let _ = resp_h.await;
}

/// TDD Step 5.7 — `pool_size_one_serializes` edge test: size=1 semaphore.
#[tokio::test]
async fn pool_size_one_serializes() {
    let bus = test_bus_with_model_node().await;

    let (sd_tx, sd_rx) = tokio::sync::oneshot::channel::<()>();
    let resp_h = spawn_responder(bus.clone(), sd_rx);

    let pool = SubagentPool::new(bus, minimal_config(), 1);

    let r1 = tokio::time::timeout(
        Duration::from_secs(5),
        pool.delegate(TaskInput {
            user_message: "x".into(),
        }),
    )
    .await
    .expect("delegate1 timed out")
    .expect("delegate1 should succeed");

    let r2 = tokio::time::timeout(
        Duration::from_secs(5),
        pool.delegate(TaskInput {
            user_message: "y".into(),
        }),
    )
    .await
    .expect("delegate2 timed out")
    .expect("delegate2 should succeed");

    assert_eq!(r1.turns_consumed, 1);
    assert_eq!(r2.turns_consumed, 1);
    assert_eq!(pool.metrics().total_delegations, 2);

    let _ = sd_tx.send(());
    let _ = resp_h.await;
}

/// Sanity check: `State::new()` works the way the pool expects.
#[test]
fn state_new_yields_default() {
    let s = State::new();
    assert_eq!(s.over_view.turn_count, 0);
    assert_eq!(s.over_view.round_count, 0);
    assert!(s.messages.is_empty());
}
