//! Phase 6 task 6.11 — McpFacade example.
//!
//! Demonstrates the multi-Bus domain controller pattern (§2.P7):
//!
//! ```text
//!     Top Bus  ──────────────────────────────  Sub Bus
//!        │                                       │
//!        │ tool_exec                             │
//!        │ ────► [DomainController Facade] ────► │
//!        │                                       │ tool_exec
//!        │                                       │ ────► [McpNode] ────► tool_result
//!        │                                       │ tool_result
//!        │ ◄── tool_result ── [DomainController] ◄─ │
//! ```
//!
//! - Top Bus: Engine + DomainController Facade
//! - Sub Bus: McpNode (filesystem-discovered tools)
//!
//! The DomainController forwards `tool_exec` from top Bus to sub Bus
//! (re-stamping the sender as itself), waits for the result, then
//! forwards the result back to the top Bus (to the original engine).
//!
//! Run: `cargo run --example domain_controller` (after adding to a workspace example).
//! Or: `cd examples/rust/domain_controller && cargo run`.

use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use arf_bus::Bus;
use arf_core::{Capability, Message, MessageFilter, NodeId, NodeInfo, Route, State, ToMatch};
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, ModelDecl, ResourceSpec};
use arf_mcp::McpNode;
use async_trait::async_trait;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

// ═══════════════════════════════════════════════════════════════════
// DomainController — facade node forwarding between two Buses
// ═══════════════════════════════════════════════════════════════════

/// A facade node that bridges Top Bus and Sub Bus.
///
/// Subscribes to `tool_exec` on Top Bus, forwards each to Sub Bus
/// (re-stamping the sender as itself), then forwards the `tool_result`
/// from Sub Bus back to Top Bus (to the original requester).
pub struct DomainController {
    pub node_id: NodeId,
    pub top_bus: Arc<Bus>,
    pub sub_bus: Arc<Bus>,
}

impl DomainController {
    pub async fn connect(&self) -> Result<arf_bus::NodeHandle> {
        // Connect to top bus: receive tool_exec requests from engine.
        let top_info = NodeInfo {
            node_id: self.node_id.clone(),
            node_type: "mcp".into(),
            capabilities: serde_json::json!({
                "kind": "domain_controller",
                "tools": [{"name": "echo", "description": "Echo back the input", "params_schema": {}}],
            }),
            online_since: now_ms(),
        };
        let top_filter = MessageFilter {
            types: Some(vec!["tool_exec".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let top_handle = self.top_bus.connect(top_info, top_filter).await?;

        // Connect to sub bus: receive tool_result from McpNode.
        let sub_info = NodeInfo {
            node_id: NodeId::new(format!("{}/sub", self.node_id)),
            node_type: "domain_controller_sub".into(),
            capabilities: serde_json::json!({"kind": "domain_controller_sub"}),
            online_since: now_ms(),
        };
        let sub_filter = MessageFilter {
            types: Some(vec!["tool_result".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let _sub_handle = self.sub_bus.connect(sub_info, sub_filter).await?;

        // Spawn the forwarding loop
        let me = self.clone_handles();
        tokio::spawn(async move { me.run_loop(top_handle).await });

        Ok(_sub_handle)
    }

    fn clone_handles(&self) -> DomainControllerHandles {
        DomainControllerHandles {
            node_id: self.node_id.clone(),
            top_bus: self.top_bus.clone(),
            sub_bus: self.sub_bus.clone(),
        }
    }
}

struct DomainControllerHandles {
    node_id: NodeId,
    top_bus: Arc<Bus>,
    sub_bus: Arc<Bus>,
}

impl DomainControllerHandles {
    async fn run_loop(self, mut top_handle: arf_bus::NodeHandle) {
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(30);
        // 1. Wait for tool_exec on top bus
        let tool_exec = loop {
            let res = tokio::select! {
                _ = tokio::time::sleep_until(stop_at) => return,
                r = top_handle.recv() => r,
            };
            let m = match res { Ok(m) => m, Err(_) => return };
            if m.msg_type == "tool_exec" {
                break m;
            }
        };

        // 2. Forward to sub bus (re-stamp from = our node_id, keep cid)
        let forwarded = Message::with_from_bus(
            tool_exec.msg_type.clone(),
            self.node_id.clone(),
            vec![], // broadcast on sub bus
            tool_exec.payload.clone(),
            self.sub_bus.id,
        );
        self.sub_bus.send(forwarded).await.ok();

        // 3. Subscribe to sub bus and wait for matching tool_result
        let mut sub_rx = self.sub_bus.subscribe();
        while let Ok(m) = tokio::time::timeout_at(stop_at, sub_rx.recv()).await {
            let m = match m { Ok(m) => m, Err(_) => return };
            if m.msg_type != "tool_result" {
                continue;
            }
            // Match cid
            let m_cid = m.payload.get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok());
            let req_cid = tool_exec.payload.get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok());
            if m_cid != req_cid {
                continue;
            }

            // 4. Forward result back to top bus (to original engine)
            let result = Message::with_from_bus(
                String::from("tool_result"),
                self.node_id.clone(),
                vec![tool_exec.from.clone()],
                m.payload.clone(),
                self.top_bus.id,
            );
            self.top_bus.send(result).await.ok();
            return;
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
            if let Some(cid) = m.payload.get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok())
            {
                let resp = Message::with_from_bus(
                    "model_response",
                    NodeId::new("model/mock"),
                    vec![],
                    serde_json::json!({
                        "correlation_id": cid.to_string(),
                        "content": "ok from mock",
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
// Main: build topology, run engine, verify
// ═══════════════════════════════════════════════════════════════════

#[tokio::main]
async fn main() -> Result<()> {
    // 1. Create tempdir with an echo tool for McpNode
    let tmp = tempfile::tempdir()?;
    let tool_dir = tmp.path().join("tools").join("echo");
    std::fs::create_dir_all(&tool_dir)?;
    std::fs::write(tool_dir.join("echo.toml"), r#"
[tool]
name = "echo"
description = "Echo back the input"
"#)?;
    std::fs::write(tool_dir.join("echo.py"), r#"
def run(args):
    return args.get("text", "")
"#)?;

    // 2. Top Bus and Sub Bus
    let top_bus = Arc::new(Bus::new(
        Duration::from_secs(1),
        Duration::from_secs(3),
        16,
    ));
    let sub_bus = Arc::new(Bus::new(
        Duration::from_secs(1),
        Duration::from_secs(3),
        16,
    ));

    // 3. McpNode on sub bus
    let mcp = McpNode::local("test", tmp.path().to_path_buf())?;
    mcp.connect(&sub_bus).await.ok();

    // 4. DomainController facade
    let dc = DomainController {
        node_id: NodeId::new("dc/main"),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
    };
    let _dc_handle = dc.connect().await?;

    // 5. Mock model responder on top bus
    // Register a dummy "model/mock" node on top bus so Strict route passes build check.
    let dummy_model = NodeInfo {
        node_id: NodeId::new("model/mock"),
        node_type: "model".into(),
        capabilities: serde_json::json!({"kind": "model", "provider": "mock"}),
        online_since: now_ms(),
    };
    let _h = top_bus
        .connect(
            dummy_model,
            MessageFilter {
                types: Some(vec!["model_call".into()]),
                to_match: ToMatch::BroadcastAndDirectedToMe,
            },
        )
        .await
        .ok();

    let mock_h = tokio::spawn({
        let bus = top_bus.clone();
        async move {
            let rx = bus.subscribe();
            run_mock_responder(rx, bus).await;
        }
    });

    // 6. Engine on top bus
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "mock".into(),
            model_name: "mock-v1".into(),
            ..Default::default()
        },
        resources: vec![ResourceSpec {
            resource_name: "dc_facade".into(),
            node_type: "mcp".into(),
            capabilities: Some(serde_json::json!({"tools": ["echo"]})),
        }],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        engine: EngineConfig {
            // model_call auto-derived from ModelDecl.provider;
            // tool_exec auto-derived from ResourceSpec pointing at dc/main.
            max_turns: 5,
            tool_timeout_ms: Some(10_000),
            ..Default::default()
        },
    };
    let mut engine = EngineBuilder::new(vec![top_bus.clone()]).build(cfg).await?;

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let output = tokio::time::timeout(
        Duration::from_secs(5),
        engine.run(&mut state, "test the facade".into(), cancel),
    )
    .await??;
    println!("Engine output: {}", output);
    println!("State messages: {}", state.messages.len());
    println!("State over_view: {:?}", state.over_view);

    mock_h.abort();
    let _ = tmp.close();
    Ok(())
}

// Tempfile re-export to avoid adding the dep twice.
mod _t {}