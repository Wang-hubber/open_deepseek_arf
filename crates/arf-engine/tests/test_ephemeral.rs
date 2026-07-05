//! Task 3 — EngineBuilder.ephemeral() flag propagation.
//!
//! The `ephemeral` flag marks an Engine as a per-task subagent. Task 4 will
//! add `reset_state()` + `run_once()` that read this flag; here we only
//! verify the flag is plumbed through the builder.

use std::sync::Arc;

use arf_core::NodeId;
use arf_engine::{AgentConfig, EngineBuilder};

/// Minimal valid bus with a `node_type == "model"` node so `EngineBuilder::build`
/// passes the ResourceRegistry model-resolution check.
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
    // The model node registered in `test_bus_with_model_node()` advertises
    // capability `provider="mock"`, so the AgentConfig must use a matching
    // provider for ResourceRegistry to resolve a model target.
    AgentConfig {
        model: arf_agent::ModelDecl {
            provider: "mock".into(),
            model_name: "mock-v1".into(),
            ..Default::default()
        },
        ..AgentConfig::default()
    }
}

#[tokio::test]
async fn ephemeral_flag_propagates() {
    let bus = test_bus_with_model_node().await;

    // ephemeral=true via builder → is_ephemeral() must report true.
    let engine = EngineBuilder::new(vec![bus.clone()])
        .ephemeral(true)
        .build(minimal_config())
        .await
        .unwrap();
    assert!(engine.is_ephemeral(), "ephemeral=true should propagate to Engine");
}

#[tokio::test]
async fn ephemeral_defaults_to_false_when_not_set() {
    let bus = test_bus_with_model_node().await;

    // No `.ephemeral(...)` call → default must be false (preserves prior behaviour).
    let engine = EngineBuilder::new(vec![bus.clone()])
        .build(minimal_config())
        .await
        .unwrap();
    assert!(!engine.is_ephemeral(), "ephemeral default must be false");
}

#[tokio::test]
async fn ephemeral_false_can_be_set_explicitly() {
    let bus = test_bus_with_model_node().await;

    let engine = EngineBuilder::new(vec![bus.clone()])
        .ephemeral(false)
        .build(minimal_config())
        .await
        .unwrap();
    assert!(!engine.is_ephemeral());
}
