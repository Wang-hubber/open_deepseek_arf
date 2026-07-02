//! ModelAdapterPoolNode — Bus facade that exposes a pool of `ModelAdapterResource`
//! as a single `node_type="model"` node (Phase 7 §3.4).
//!
//! External view (Engine sees one node): `node_type="model"` with
//! `capabilities.provider = advertised_provider` and `capabilities.models =
//! advertised_models`. Auto-discovery in `ResourceRegistry::resolve_model`
//! finds it and routes `model_call` directly.
//!
//! Internal view: a bounded pool of `ModelAdapterResource` provides
//! backpressure (Queue/Reject overflow) + connection reuse. Each request
//! acquires a lease, the request is forwarded to the sub-bus, the response
//! is forwarded back, and the lease is released on drop.
//!
//! Sub-bus assumption: at least one node with `node_type="model"` (the
//! actual ModelAdapterNode with the real Provider) is connected and
//! responds to `model_call` / emits `model_response`.

use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo, ToMatch};
use arf_pool::Pool;
use serde_json::json;
use uuid::Uuid;

use crate::ModelAdapterResource;

/// A Bus facade that bridges `model_call` on a top bus to a pool of
/// `ModelAdapterResource` on a sub bus.
///
/// Registers itself as `node_type="model"` on the top bus so the Engine
/// auto-discovers it via `Registry::resolve_model(provider)`.
pub struct ModelAdapterPoolNode {
    pub node_id: NodeId,
    pub top_bus: Arc<Bus>,
    pub sub_bus: Arc<Bus>,
    pub pool: Arc<Pool<ModelAdapterResource>>,
    /// `capabilities.provider` advertised to the Engine — must match the
    /// `ModelDecl.provider` in `AgentConfig.model`.
    pub advertised_provider: String,
    /// `capabilities.models` advertised to the Engine.
    pub advertised_models: Vec<String>,
}

impl ModelAdapterPoolNode {
    /// Connect to both buses and spawn the forwarding loop.
    pub async fn connect(self: Arc<Self>) -> Result<(), arf_bus::ConnectError> {
        let top_info = NodeInfo {
            node_id: self.node_id.clone(),
            node_type: "model".into(),
            capabilities: json!({
                "provider": self.advertised_provider,
                "models": self.advertised_models,
                "kind": "model_adapter_pool",
            }),
            online_since: now_ms(),
        };
        let top_filter = MessageFilter {
            types: Some(vec!["model_call".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let top_handle = self.top_bus.connect(top_info, top_filter).await?;

        let sub_id = NodeId::new(format!("{}/sub", self.node_id));
        let sub_info = NodeInfo {
            node_id: sub_id.clone(),
            node_type: "model_pool_sub".into(),
            capabilities: json!({"kind": "model_pool_sub"}),
            online_since: now_ms(),
        };
        let sub_filter = MessageFilter {
            types: Some(vec!["model_response".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let sub_handle = self.sub_bus.connect(sub_info, sub_filter).await?;

        let me = self.clone();
        tokio::spawn(async move { me.run_loop(top_handle, sub_handle).await });

        Ok(())
    }

    async fn run_loop(
        self: Arc<Self>,
        mut top_handle: arf_bus::NodeHandle,
        mut sub_handle: arf_bus::NodeHandle,
    ) {
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(300);
        loop {
            let req = tokio::select! {
                _ = tokio::time::sleep_until(stop_at) => return,
                r = top_handle.recv() => match r {
                    Ok(m) => m,
                    Err(_) => return,
                }
            };
            if req.msg_type != "model_call" {
                continue;
            }

            let lease = match self.pool.acquire().await {
                Ok(l) => l,
                Err(_) => return,
            };

            let sub_id = NodeId::new(format!("{}/sub", self.node_id));
            let cid = req
                .payload
                .get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok());
            let forwarded = Message::with_from_bus(
                req.msg_type.clone(),
                sub_id,
                vec![],
                req.payload.clone(),
                self.sub_bus.id,
            );
            let _ = self.sub_bus.send(forwarded).await;

            while let Ok(m) = tokio::time::timeout_at(stop_at, sub_handle.recv()).await {
                let m = match m {
                    Ok(m) => m,
                    Err(_) => return,
                };
                if m.msg_type != "model_response" {
                    continue;
                }
                let m_cid = m
                    .payload
                    .get("correlation_id")
                    .and_then(|v| v.as_str())
                    .and_then(|s| Uuid::parse_str(s).ok());
                if m_cid == cid {
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