//! PoolNode — Bus bridge for pooled resources (Phase 6 §2.P10 / task 6.16).
//!
//! A `PoolNode` is a Bus Node that subscribes to `model_call` (or
//! `tool_exec`) on a top Bus, forwards each to a sub Bus where actual
//! resource handlers live, then forwards the response back to the top Bus
//! with `correlation_id` / `from_bus` preserved.
//!
//! This is the App-level implementation of the "PoolNode" pattern: the
//! framework provides the routing primitive (Engine, Bus); App wires a
//! facade that translates high-level intent into low-level resource access.

use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo, ToMatch};
use arf_core::NodeId as _;

use crate::{Pool, Resource};

/// A Bus Node that bridges a top Bus to a sub Bus via a [`Pool`].
///
/// Note: this is a minimal implementation that demonstrates the pattern.
/// Real apps would couple PoolNode with concrete resource implementations
/// (e.g., `ModelAdapterResource` — task 6.17, `McpResource` — task 6.18).
pub struct PoolNode<R: crate::Resource> {
    pub node_id: NodeId,
    pub top_bus: Arc<Bus>,
    pub sub_bus: Arc<Bus>,
    pub pool: Arc<Pool<R>>,
}

impl<R: crate::Resource + 'static> PoolNode<R> {
    /// Connect to both Buses; spawn the forwarding loop.
    pub async fn connect(self: Arc<Self>) {
        let info = NodeInfo {
            node_id: self.node_id.clone(),
            node_type: "pool_node".into(),
            capabilities: serde_json::json!({
                "kind": "pool_node",
            }),
            online_since: now_ms(),
        };
        let filter = MessageFilter {
            types: Some(vec!["model_call".into(), "tool_exec".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let handle = self.top_bus.connect(info, filter).await.expect("connect top");
        let sub_handle = self
            .sub_bus
            .connect(
                NodeInfo {
                    node_id: NodeId::new(format!("{}/sub", self.node_id)),
                    node_type: "pool_node_sub".into(),
                    capabilities: serde_json::json!({"kind": "pool_node_sub"}),
                    online_since: now_ms(),
                },
                MessageFilter {
                    types: Some(vec!["model_response".into(), "tool_result".into()]),
                    to_match: ToMatch::BroadcastAndDirectedToMe,
                },
            )
            .await
            .expect("connect sub");

        let me = self.clone();
        tokio::spawn(async move { me.run_loop(handle, sub_handle).await });
    }

    async fn run_loop(
        self: Arc<Self>,
        mut top_handle: arf_bus::NodeHandle,
        mut sub_handle: arf_bus::NodeHandle,
    ) {
        let stop_at = tokio::time::Instant::now() + std::time::Duration::from_secs(30);
        loop {
            // Wait for request on top bus
            let req = tokio::select! {
                _ = tokio::time::sleep_until(stop_at) => return,
                r = top_handle.recv() => match r {
                    Ok(m) => m,
                    Err(_) => return,
                }
            };
            if !matches!(req.msg_type.as_str(), "model_call" | "tool_exec") {
                continue;
            }
            // Acquire a resource from pool
            let lease = match self.pool.acquire().await {
                Ok(l) => l,
                Err(_) => return,
            };
            // Forward to sub bus. The `from` field MUST be our sub-bus
            // node_id (`{top}/sub`) — the ModelAdapterNode on the sub bus
            // replies to `msg.from`, and our sub_handle subscribes under
            // the sub-bus node_id. Using `self.node_id` (the top-bus id)
            // would route the response to a nonexistent address and the
            // sub_handle would never see it.
            let sub_id = NodeId::new(format!("{}/sub", self.node_id));
            let cid = req
                .payload
                .get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| uuid::Uuid::parse_str(s).ok());
            let forwarded = Message::with_from_bus(
                req.msg_type.clone(),
                sub_id,
                vec![],
                req.payload.clone(),
                self.sub_bus.id,
            );
            let _ = self.sub_bus.send(forwarded).await;
            // Wait for response on sub bus
            while let Ok(m) = tokio::time::timeout_at(stop_at, sub_handle.recv()).await {
                let m = match m { Ok(m) => m, Err(_) => return };
                if !matches!(m.msg_type.as_str(), "model_response" | "tool_result") {
                    continue;
                }
                let m_cid = m
                    .payload
                    .get("correlation_id")
                    .and_then(|v| v.as_str())
                    .and_then(|s| uuid::Uuid::parse_str(s).ok());
                if m_cid == cid {
                    // Forward back to top bus
                    let back = Message::with_from_bus(
                        m.msg_type.clone(),
                        self.node_id.clone(),
                        vec![req.from.clone()],
                        m.payload.clone(),
                        self.top_bus.id,
                    );
                    let _ = self.top_bus.send(back).await;
                    break;
                }
            }
            drop(lease);
        }
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
    .unwrap_or_default()
    .as_millis() as u64
}