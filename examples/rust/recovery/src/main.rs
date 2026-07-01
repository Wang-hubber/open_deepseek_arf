//! Phase 6 task 6.12 — App-level Recovery example.
//!
//! Demonstrates the App-level Recovery pattern (§5.6 / §2.P9):
//!
//! - AppCheckpoint Node — a Bus Node that periodically snapshots Engine State
//!   to a file. Triggered via a CheckpointRule (RoundEnd) that publishes a
//!   `app_checkpoint` Query message; AppCheckpoint responds with the latest
//!   serialized State.
//!
//! - Bus::barrier() — coordinated snapshot across multiple nodes. Engine
//!   publishes barrier_request; all connected nodes ack; Engine proceeds only
//!   when all acks received (or timeout).
//!
//! - File persistence — AppCheckpoint writes `data/checkpoint_<id>.json`.
//!   On restart, App can deserialize and rehydrate Engine.state before
//!   `engine.run(...)`.
//!
//! Run: `cd examples/recovery && cargo run`.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use arf_bus::{BarrierReceipt, Bus};
use arf_core::{
    ActionMessage, Message, MessageFilter, NodeId, NodeInfo, State, ToMatch,
};
use arf_engine::{AgentConfig, Engine, EngineBuilder, ModelConfig, WaitStrategy};
use async_trait::async_trait;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

// ═══════════════════════════════════════════════════════════════════
// AppCheckpointAction — the Query message Engine publishes
// ═══════════════════════════════════════════════════════════════════

/// Engine → AppCheckpoint: "snapshot the latest State and respond".
///
/// Query intent so the engine parks until AppCheckpoint replies with the
/// serialized State payload.
#[derive(Debug, Clone)]
struct AppCheckpointAction {
    cid: Uuid,
}

#[async_trait]
impl ActionMessage for AppCheckpointAction {
    fn msg_type(&self) -> &'static str {
        "app_checkpoint"
    }
    fn correlation_id(&self) -> Uuid {
        self.cid
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::json!({"correlation_id": self.cid.to_string()})
    }
    fn intent(&self) -> arf_core::MessageIntent {
        arf_core::MessageIntent::Query
    }
}

// ═══════════════════════════════════════════════════════════════════
// AppCheckpoint Node — snapshot Engine State to file
// ═══════════════════════════════════════════════════════════════════

/// A Bus Node that listens for `app_checkpoint` requests, writes the State
/// (received in the request payload? — here simplified: app provides state
/// via a shared channel) to a file, and responds with a confirmation.
pub struct AppCheckpoint {
    pub node_id: NodeId,
    pub bus: Arc<Bus>,
    pub data_dir: PathBuf,
}

impl AppCheckpoint {
    pub async fn connect(self: Arc<Self>) -> Result<()> {
        let info = NodeInfo {
            node_id: self.node_id.clone(),
            node_type: "app_checkpoint".into(),
            capabilities: serde_json::json!({"kind": "app_checkpoint"}),
            online_since: now_ms(),
        };
        let filter = MessageFilter {
            types: Some(vec!["app_checkpoint".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let mut handle = self.bus.connect(info, filter).await?;
        let me = self.clone();
        tokio::spawn(async move { me.run_loop(handle).await });
        Ok(())
    }

    async fn run_loop(self: Arc<Self>, mut handle: arf_bus::NodeHandle) {
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(30);
        while let Ok(m) = tokio::time::timeout_at(stop_at, handle.recv()).await {
            let m = match m { Ok(m) => m, Err(_) => return };
            if m.msg_type != "app_checkpoint" {
                continue;
            }
            let cid = m
                .payload
                .get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok());
            if let Some(cid) = cid {
                // Write a marker file (in real app: serialize State).
                std::fs::create_dir_all(&self.data_dir).ok();
                let path = self.data_dir.join(format!("checkpoint_{}.json", cid));
                let content = serde_json::json!({
                    "checkpoint_id": cid.to_string(),
                    "timestamp": now_ms(),
                    "node_id": self.node_id.as_str(),
                });
                std::fs::write(&path, serde_json::to_string_pretty(&content).unwrap()).ok();
                println!("[AppCheckpoint] wrote {}", path.display());

                // Reply with the (placeholder) serialized state.
                let resp = Message::with_from_bus(
                    String::from("app_checkpoint_result"),
                    self.node_id.clone(),
                    vec![m.from.clone()],
                    serde_json::json!({
                        "correlation_id": cid.to_string(),
                        "path": path.to_string_lossy(),
                        "size": content.to_string().len(),
                    }),
                    self.bus.id,
                );
                let _ = self.bus.send(resp).await;
            }
        }
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

// ═══════════════════════════════════════════════════════════════════
// Mock model responder
// ═══════════════════════════════════════════════════════════════════

async fn run_mock_responder(
    mut rx: tokio::sync::broadcast::Receiver<Message>,
    bus: Arc<Bus>,
) {
    let stop_at = tokio::time::Instant::now() + Duration::from_secs(10);
    while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
        let m = match m { Ok(m) => m, Err(_) => break };
        if m.msg_type == "model_call" {
            if let Some(cid) = m
                .payload
                .get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok())
            {
                let resp = Message::with_from_bus(
                    String::from("model_response"),
                    NodeId::new("model/mock"),
                    vec![],
                    serde_json::json!({
                        "correlation_id": cid.to_string(),
                        "content": "ok",
                    }),
                    bus.id,
                );
                let _ = bus.send(resp).await;
                break;
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// Barrier receiver — respond to barrier_request with ack
// ═══════════════════════════════════════════════════════════════════

async fn run_barrier_responder(
    mut rx: tokio::sync::broadcast::Receiver<Message>,
    bus: Arc<Bus>,
    node_id: NodeId,
) {
    let stop_at = tokio::time::Instant::now() + Duration::from_secs(10);
    while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
        let m = match m { Ok(m) => m, Err(_) => return };
        if m.msg_type != "barrier_request" {
            continue;
        }
        if let Some(cid) = m
            .payload
            .get("correlation_id")
            .and_then(|v| v.as_str())
            .and_then(|s| Uuid::parse_str(s).ok())
        {
            let ack = Message::with_from_bus(
                String::from("barrier_ack"),
                node_id.clone(),
                vec![],
                serde_json::json!({"correlation_id": cid.to_string()}),
                bus.id,
            );
            let _ = bus.send(ack).await;
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// Main
// ═══════════════════════════════════════════════════════════════════

#[tokio::main]
async fn main() -> Result<()> {
    let data_dir = std::env::current_dir()?.join("data_recovery");
    let _ = std::fs::remove_dir_all(&data_dir);

    let bus = Arc::new(Bus::new(
        Duration::from_secs(1),
        Duration::from_secs(3),
        16,
    ));

    // AppCheckpoint node
    let cp_node = Arc::new(AppCheckpoint {
        node_id: NodeId::new("cp/main"),
        bus: bus.clone(),
        data_dir: data_dir.clone(),
    });
    let _cp_handle = cp_node.clone().connect().await?; // returns () now

    // Mock model + a second barrier ack node
    let _h2 = bus
        .connect(
            NodeInfo {
                node_id: NodeId::new("model/mock"),
                node_type: "model".into(),
                capabilities: serde_json::json!({"kind": "model"}),
                online_since: now_ms(),
            },
            MessageFilter {
                types: Some(vec!["model_call".into()]),
                to_match: ToMatch::BroadcastAndDirectedToMe,
            },
        )
        .await?;
    let _h3 = bus
        .connect(
            NodeInfo {
                node_id: NodeId::new("worker/2"),
                node_type: "worker".into(),
                capabilities: serde_json::json!({"kind": "worker"}),
                online_since: now_ms(),
            },
            MessageFilter {
                types: Some(vec!["barrier_request".into()]),
                to_match: ToMatch::BroadcastAndDirectedToMe,
            },
        )
        .await?;

    // Spawn responders
    let mock_h = tokio::spawn({
        let bus = bus.clone();
        async move {
            let rx = bus.subscribe();
            run_mock_responder(rx, bus).await;
        }
    });
    let barrier_h = tokio::spawn({
        let bus = bus.clone();
        async move {
            let rx = bus.subscribe();
            run_barrier_responder(rx, bus, NodeId::new("worker/2")).await;
        }
    });

    // Engine with RoundEnd checkpoint rule that requests an app checkpoint
    let cp_action = AppCheckpointAction {
        cid: Uuid::new_v4(),
    };
    let mut cfg = AgentConfig {
        agent_id: "recoverable".into(),
        model_config: ModelConfig { provider: "mock".into(), model: "mock-v1".into() },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        max_turns: 5,
        tool_timeout_ms: Some(10_000),
        permissions: Default::default(),
        routes: {
            let mut r = std::collections::HashMap::new();
            r.insert("model_call".into(), arf_core::Route::strict(vec![NodeId::new("model/mock")]));
            r.insert("app_checkpoint".into(), arf_core::Route::strict(vec![NodeId::new("cp/main")]));
            r
        },
        checkpoint_rules: vec![arf_core::CheckpointRule::new(
            "round_end_checkpoint",
            arf_core::Checkpoint::RoundEnd,
            |_s| true,
            move |_s| Box::new(cp_action.clone()) as Box<dyn ActionMessage>,
        )],
        processors: Default::default(),
        on_member_failed: None,
        tools_include: None,
        tools_exclude: vec![],
        skills_include: None,
        skills_exclude: vec![],
    };
    let mut engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await?;

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let output = tokio::time::timeout(
        Duration::from_secs(5),
        engine.run(&mut state, "save my progress".into(), cancel),
    )
    .await??;
    println!("Engine output: {}", output);
    println!("State: round={}, turn={}", state.over_view.round_count, state.over_view.turn_count);

    // Demonstrate barrier() — coordinated snapshot across all nodes
    println!("\nRunning barrier()...");
    let barrier_receipt = bus.barrier(
        vec![NodeId::new("cp/main"), NodeId::new("worker/2")],
        Duration::from_secs(2),
    ).await;
    println!(
        "Barrier: acked={} missing={} timed_out={}",
        barrier_receipt.acked.len(),
        barrier_receipt.missing.len(),
        barrier_receipt.timed_out
    );

    // List checkpoint files written
    println!("\nCheckpoint files in {}:", data_dir.display());
    if let Ok(entries) = std::fs::read_dir(&data_dir) {
        for e in entries.flatten() {
            println!("  {}", e.path().display());
        }
    }

    mock_h.abort();
    barrier_h.abort();
    Ok(())
}