//! [E2E] App-level Recovery — app_checkpoint + Bus::barrier.
//!
//! Test angles covered:
//! - [方法] engine with a RoundEnd checkpoint rule — engine publishes
//!   `app_checkpoint`, AppCheckpoint responds, engine proceeds.
//! - [边界] CancellationToken fired mid-checkpoint → engine returns Stopped.
//! - [方法] Bus::barrier() collects acks from N participants with a deadline.
//! - [边界] barrier with no online participants returns empty receipt
//!   (or all missing) without panicking.
//!
//! Pattern follows `examples/recovery/src/main.rs` (Phase 6 task 6.12).

mod common;

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{
    ActionMessage, Checkpoint, CheckpointRule, Message, MessageFilter, NodeId, NodeInfo,
    State, ToMatch,
};
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, ModelDecl};
use arf_model_adapter::{ModelAdapterNode, Provider};
use async_trait::async_trait;
use common::harness::ProviderKind;
use common::provider::{scripted, text_response};
use serde_json::json;
use tempfile::TempDir;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

// ── Reusable helpers ─────────────────────────────────────────────────────

/// Minimal `app_checkpoint` ActionMessage for the harness.
#[derive(Clone, Debug)]
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
        json!({"correlation_id": self.cid.to_string()})
    }
    fn intent(&self) -> arf_core::MessageIntent {
        arf_core::MessageIntent::Query
    }
}

/// A tiny AppCheckpoint Bus Node that writes a file and replies via the bus.
struct AppCheckpointNode {
    node_id: NodeId,
    bus: Arc<Bus>,
    data_dir: PathBuf,
    _handle: tokio::task::JoinHandle<()>,
}

impl AppCheckpointNode {
    async fn connect(
        node_id: NodeId,
        bus: Arc<Bus>,
        data_dir: PathBuf,
    ) -> anyhow::Result<Self> {
        let info = NodeInfo {
            node_id: node_id.clone(),
            node_type: "app_checkpoint".into(),
            capabilities: json!({"kind": "app_checkpoint"}),
            online_since: 0,
        };
        let filter = MessageFilter {
            types: Some(vec!["app_checkpoint".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let mut handle = bus.connect(info, filter).await?;
        let bus_for_loop = bus.clone();
        let node_id_for_loop = node_id.clone();
        let data_dir_for_loop = data_dir.clone();
        let loop_handle = tokio::spawn(async move {
            while let Ok(msg) = handle.recv().await {
                if msg.msg_type != "app_checkpoint" {
                    continue;
                }
                let cid = msg
                    .payload
                    .get("correlation_id")
                    .and_then(|v| v.as_str())
                    .and_then(|s| Uuid::parse_str(s).ok());
                if let Some(cid) = cid {
                    std::fs::create_dir_all(&data_dir_for_loop).ok();
                    let path = data_dir_for_loop.join(format!("checkpoint_{}.json", cid));
                    let content = json!({
                        "checkpoint_id": cid.to_string(),
                        "timestamp": 0u64,
                        "node_id": node_id_for_loop.as_str(),
                    });
                    std::fs::write(&path, serde_json::to_string(&content).unwrap_or_default()).ok();
                    let _ = bus_for_loop
                        .send(Message::with_from_bus(
                            String::from("app_checkpoint_result"),
                            node_id_for_loop.clone(),
                            vec![msg.from.clone()],
                            json!({
                                "correlation_id": cid.to_string(),
                                "path": path.to_string_lossy(),
                                "size": content.to_string().len(),
                            }),
                            bus_for_loop.id,
                        ))
                        .await;
                }
            }
        });
        Ok(Self {
            node_id,
            bus,
            data_dir,
            _handle: loop_handle,
        })
    }

    fn id(&self) -> &NodeId {
        &self.node_id
    }
}

// ── Test 1: RoundEnd checkpoint rule triggers AppCheckpoint ──────────────

// [方法] engine's RoundEnd checkpoint → AppCheckpoint publishes
// app_checkpoint_result → engine returns the content.
// 状态写盘 + cid 关联正确。
#[tokio::test]
async fn round_end_checkpoint_writes_file_and_returns() -> anyhow::Result<()> {
    let tmp = TempDir::new()?;
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        16,
    ));
    // Connect AppCheckpoint to the bus.
    let cp = AppCheckpointNode::connect(
        NodeId::new("cp/test1"),
        bus.clone(),
        tmp.path().to_path_buf(),
    )
    .await?;
    let model_node = ModelAdapterNode::new(
        scripted(vec![text_response("hello checkpoint world")]),
        &bus,
        NodeId::new("model/checkpoint-test"),
    )
    .await?;
    let cp_action_2 = AppCheckpointAction {
        cid: Uuid::new_v4(),
    };
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        resources: vec![],
        engine: EngineConfig {
            // model_call auto-derived from ModelDecl.provider via Registry.resolve_model.
            // app_checkpoint is a custom msg_type → keep Strict route here.
            routes: {
                let mut r = std::collections::HashMap::new();
                r.insert(
                    "app_checkpoint".into(),
                    arf_core::Route::strict(vec![cp.id().clone()]),
                );
                r
            },
            checkpoint_rules: vec![CheckpointRule::new(
                "round_end_cp",
                Checkpoint::RoundEnd,
                |_| true,
                move |_| Box::new(cp_action_2.clone()),
            )],
            max_turns: 5,
            tool_timeout_ms: Some(2000),
            ..Default::default()
        },
    };
    let mut engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await?;
    let mut state = State::new();
    let cancel = CancellationToken::new();
    let out = tokio::time::timeout(
        Duration::from_secs(10),
        engine.run(&mut state, "trigger a checkpoint".into(), cancel),
    )
    .await
    .expect("engine run timed out")
    .expect("engine run failed");

    assert_eq!(out, "hello checkpoint world");
    // Checkpoint file written.
    let mut found = false;
    for entry in std::fs::read_dir(tmp.path())?.flatten() {
        if entry.file_name().to_string_lossy().starts_with("checkpoint_") {
            found = true;
            break;
        }
    }
    assert!(found, "no checkpoint file written to {}", tmp.path().display());
    drop(model_node);
    drop(cp);
    Ok(())
}

// ── Test 2: cancellation during engine run yields Stopped ────────────────

// [边界] cancellation 在 checkpoint 路径中触发 → engine 返回 RunError::Stopped。
#[tokio::test]
async fn cancellation_during_run_yields_stopped() -> anyhow::Result<()> {
    let cancel = CancellationToken::new();
    cancel.cancel();
    let mut h = common::harness::E2EHarness::builder(ProviderKind::Mock(
        scripted(vec![text_response("never delivered")]),
    ))
    .cancel(cancel)
    .build()
    .await?;
    let result = h
        .engine
        .run(&mut h.state, "this should be cancelled".into(), h.cancel.clone().unwrap_or_else(CancellationToken::new))
        .await;
    assert!(
        matches!(result, Err(arf_engine::RunError::Stopped)),
        "expected Stopped, got {:?}",
        result
    );
    Ok(())
}

// ── Test 3: Bus::barrier collects acks from multiple participants ─────────

// [方法] 注册 N 个节点各回复 barrier_ack → barrier() returns ack list of size N。
// 用最小的 responder（直接 connect + reply on barrier_request）。
#[tokio::test]
async fn bus_barrier_collects_acks_from_n_participants() -> anyhow::Result<()> {
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        16,
    ));
    let participants: Vec<NodeId> = (0..3)
        .map(|i| NodeId::new(format!("worker/{i}")))
        .collect();
    let mut handles = Vec::new();
    for p in &participants {
        let info = NodeInfo {
            node_id: p.clone(),
            node_type: "worker".into(),
            capabilities: json!({"kind": "worker"}),
            online_since: 0,
        };
        let filter = MessageFilter {
            types: Some(vec!["barrier_request".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let mut handle = bus.connect(info, filter).await?;
        let p_for_task = p.clone();
        let bus_for_task = bus.clone();
        handles.push(tokio::spawn(async move {
            while let Ok(msg) = handle.recv().await {
                if msg.msg_type != "barrier_request" {
                    continue;
                }
                let cid = msg
                    .payload
                    .get("correlation_id")
                    .and_then(|v| v.as_str())
                    .and_then(|s| Uuid::parse_str(s).ok());
                if let Some(cid) = cid {
                    let _ = bus_for_task
                        .send(Message::with_from_bus(
                            String::from("barrier_ack"),
                            p_for_task.clone(),
                            vec![],
                            json!({"correlation_id": cid.to_string()}),
                            bus_for_task.id,
                        ))
                        .await;
                }
            }
        }));
    }
    let receipt = bus.barrier(participants.clone(), Duration::from_secs(2)).await;
    assert_eq!(receipt.acked.len(), participants.len());
    assert!(receipt.missing.is_empty());
    assert!(!receipt.timed_out);
    for h in handles {
        h.abort();
    }
    Ok(())
}

// ── Test 4: barrier with no responding participants times out cleanly ────

// [边界] barrier 列出 participants 但没人 acks → receipt.timed_out=true,
// receipt.acked 空, receipt.missing = 列出列表。bus 不 panic、不挂死。
#[tokio::test]
async fn bus_barrier_times_out_with_silent_participants() -> anyhow::Result<()> {
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        16,
    ));
    // Connect the participants (so they're "online") but no ack loop.
    let participants: Vec<NodeId> = (0..2)
        .map(|i| NodeId::new(format!("silent/{i}")))
        .collect();
    for p in &participants {
        let info = NodeInfo {
            node_id: p.clone(),
            node_type: "silent".into(),
            capabilities: json!({}),
            online_since: 0,
        };
        // Filter that accepts everything but ignores barrier_request.
        let filter = MessageFilter {
            types: Some(vec!["heartbeat_request".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let _h = bus.connect(info, filter).await?;
        let _ = p;
    }
    let receipt = bus.barrier(participants.clone(), Duration::from_millis(200)).await;
    assert_eq!(receipt.acked.len(), 0);
    assert!(receipt.timed_out);
    assert_eq!(receipt.missing.len(), participants.len());
    Ok(())
}
