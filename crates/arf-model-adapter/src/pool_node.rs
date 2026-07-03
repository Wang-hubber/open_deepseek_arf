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
//!
//! ## Concurrency (Phase 9 F-003)
//!
//! The forwarding loop is a **dispatcher**, not a serial worker. Each
//! `model_call` is handled on its own `tokio::spawn`ed task that acquires a
//! lease, forwards to the sub-bus, and awaits its response. This lets N
//! concurrent `model_call`s use up to `max_size` pooled resources in parallel
//! (a serial loop would block on each response and use only one resource at a
//! time regardless of `max_size`).
//!
//! Because `NodeHandle` is a single-consumer receiver (not clonable), a
//! **demultiplexer** task owns the sub-bus handle: it receives every
//! `model_response` and routes it — by `correlation_id` — to the waiting
//! per-request task via a `oneshot` channel.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::msg_type::{MODEL_CALL, MODEL_RESPONSE};
use arf_core::{Message, MessageFilter, NodeId, NodeInfo, ToMatch};
use arf_pool::Pool;
use serde_json::json;
use tokio::sync::{oneshot, Mutex};
use uuid::Uuid;

use crate::ModelAdapterResource;

/// Map of in-flight requests: `correlation_id → oneshot sender` for the
/// matching `model_response`. Shared between the dispatcher (inserts) and the
/// demux task (removes + fulfils).
type PendingMap = Arc<Mutex<HashMap<Uuid, oneshot::Sender<Message>>>>;

/// How long a per-request task waits for its `model_response` before giving up
/// and returning an error to the caller.
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(120);

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
            types: Some(vec![MODEL_CALL.into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let top_handle = self.top_bus.connect(top_info, top_filter).await?;

        let sub_id = self.sub_id();
        let sub_info = NodeInfo {
            node_id: sub_id.clone(),
            node_type: "model_pool_sub".into(),
            capabilities: json!({"kind": "model_pool_sub"}),
            online_since: now_ms(),
        };
        let sub_filter = MessageFilter {
            types: Some(vec![MODEL_RESPONSE.into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let sub_handle = self.sub_bus.connect(sub_info, sub_filter).await?;

        let me = self.clone();
        tokio::spawn(async move { me.run_loop(top_handle, sub_handle).await });

        Ok(())
    }

    /// The sub-bus identity this facade forwards from.
    fn sub_id(&self) -> NodeId {
        NodeId::new(format!("{}/sub", self.node_id))
    }

    /// Dispatcher loop: demux sub-bus responses in a background task, then spawn
    /// a per-request task for every incoming `model_call`.
    async fn run_loop(
        self: Arc<Self>,
        mut top_handle: arf_bus::NodeHandle,
        mut sub_handle: arf_bus::NodeHandle,
    ) {
        let pending: PendingMap = Arc::new(Mutex::new(HashMap::new()));

        // Demux task: owns the sub handle, routes each model_response to the
        // waiting per-request task by correlation_id.
        let pending_demux = pending.clone();
        let demux = tokio::spawn(async move {
            while let Ok(m) = sub_handle.recv().await {
                if m.msg_type != MODEL_RESPONSE {
                    continue;
                }
                if let Some(cid) = extract_cid(&m) {
                    if let Some(tx) = pending_demux.lock().await.remove(&cid) {
                        let _ = tx.send(m);
                    }
                }
            }
        });

        let stop_at = tokio::time::Instant::now() + Duration::from_secs(300);
        loop {
            let req = tokio::select! {
                _ = tokio::time::sleep_until(stop_at) => break,
                r = top_handle.recv() => match r {
                    Ok(m) => m,
                    Err(_) => break,
                }
            };
            if req.msg_type != MODEL_CALL {
                continue;
            }

            // Every model_call carries a correlation_id (ModelCall serializes it
            // as a required field). Without one we cannot demux the response.
            let cid = match extract_cid(&req) {
                Some(c) => c,
                None => {
                    self.send_error_back(&req, None, "model_call missing correlation_id")
                        .await;
                    continue;
                }
            };

            let (tx, rx) = oneshot::channel();
            pending.lock().await.insert(cid, tx);

            let me = self.clone();
            let pending = pending.clone();
            tokio::spawn(async move {
                me.handle_one_model_call(req, cid, rx, pending).await;
            });
        }

        demux.abort();
    }

    /// Handle a single `model_call`: acquire a lease, forward to the sub-bus,
    /// await the matching `model_response` (via the demux `oneshot`), forward it
    /// back to the caller, and release the lease on drop.
    async fn handle_one_model_call(
        self: Arc<Self>,
        req: Message,
        cid: Uuid,
        rx: oneshot::Receiver<Message>,
        pending: PendingMap,
    ) {
        let lease = match self.pool.acquire().await {
            Ok(l) => l,
            Err(e) => {
                // Drop our pending registration and tell the caller — never fail
                // silently or leave the Engine parked forever.
                pending.lock().await.remove(&cid);
                self.send_error_back(&req, Some(cid), &format!("pool acquire: {e}"))
                    .await;
                return;
            }
        };

        let forwarded = Message::with_from_bus(
            req.msg_type.clone(),
            self.sub_id(),
            vec![],
            req.payload.clone(),
            self.sub_bus.id,
        );
        let _ = self.sub_bus.send(forwarded).await;

        match tokio::time::timeout(RESPONSE_TIMEOUT, rx).await {
            Ok(Ok(resp)) => {
                let back = Message::with_from_bus(
                    MODEL_RESPONSE.to_string(),
                    self.node_id.clone(),
                    vec![req.from.clone()],
                    resp.payload.clone(),
                    self.top_bus.id,
                );
                let _ = self.top_bus.send(back).await;
            }
            _ => {
                // Timeout or demux dropped the sender: clean up + report error.
                pending.lock().await.remove(&cid);
                self.send_error_back(&req, Some(cid), "model_call timed out waiting for response")
                    .await;
            }
        }

        drop(lease);
    }

    /// Send a `model_response` carrying an `error` field back to the request's
    /// origin, echoing the correlation_id so the Engine can match it.
    async fn send_error_back(&self, req: &Message, cid: Option<Uuid>, error: &str) {
        let mut payload = json!({ "error": error, "finish_reason": "error" });
        if let Some(cid) = cid {
            payload["correlation_id"] = json!(cid.to_string());
        }
        let back = Message::with_from_bus(
            MODEL_RESPONSE.to_string(),
            self.node_id.clone(),
            vec![req.from.clone()],
            payload,
            self.top_bus.id,
        );
        let _ = self.top_bus.send(back).await;
    }
}

/// Extract the `correlation_id` (a UUID string) from a message payload.
fn extract_cid(msg: &Message) -> Option<Uuid> {
    msg.payload
        .get("correlation_id")
        .and_then(|v| v.as_str())
        .and_then(|s| Uuid::parse_str(s).ok())
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
