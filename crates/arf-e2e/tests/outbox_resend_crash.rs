//! Task 17 — End-to-end crash → restart → outbox resend verification.
//!
//! Implements spec §2.4 recovery path step 4-5:
//!   4. Scan all peer_message_sent, remove those with peer_reply_received
//!   5. Remaining (corr_id, target, payload) → re-bus.send()
//!
//! Test 1: kill the Engine mid-flight (no reply), drop it, rebuild with
//!         the same session_id + store → resend re-fires the message;
//!         store records attempt=2.
//! Test 2: completed cid (sent + reply recorded before restart) must NOT
//!         be resent on the second Engine instance.

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, ModelMessage, NodeId, Route};
use arf_engine::{
    AgentConfig, EngineBuilder, EngineConfig, ModelDecl,
};
use arf_model_adapter::types::{ModelResponsePayload, ToolDef, Usage};
use arf_model_adapter::{ModelAdapterNode, Provider, ProviderError};
use arf_model_adapter::types::ModelParams;
use arf_session::{JsonlSessionStore, PendingPeerMessage, SessionStore};
use async_trait::async_trait;

struct NoopProvider {
    name: &'static str,
    model: String,
}
#[async_trait]
impl Provider for NoopProvider {
    fn name(&self) -> &str {
        self.name
    }
    fn supported_models(&self) -> &[String] {
        std::slice::from_ref(&self.model)
    }
    async fn chat(
        &self,
        _m: &str,
        _msgs: Vec<ModelMessage>,
        _t: Vec<ToolDef>,
        _p: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        Ok(ModelResponsePayload {
            message: ModelMessage::new("assistant", ""),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage {
                input_tokens: 1,
                output_tokens: 1,
                total_tokens: 2,
            }),
            id: "x".into(),
            model: self.model.clone(),
        })
    }
}

fn noop_provider(name: &'static str) -> Arc<NoopProvider> {
    Arc::new(NoopProvider {
        name,
        model: "n".to_string(),
    })
}

fn make_cfg(provider_name: &str) -> AgentConfig {
    let mut routes = HashMap::new();
    routes.insert("peer_message".into(), Route::Strict(vec![]));
    AgentConfig {
        model: ModelDecl {
            provider: provider_name.into(),
            model_name: "n".into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "x".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        engine: EngineConfig {
            routes,
            checkpoint_rules: vec![],
            processors: HashMap::new(),
            on_member_failed: None,
            max_turns: 3,
            tool_timeout_ms: Some(3_000),
        },
        tools: vec![],
    }
}

async fn register_target(bus: &Bus, id: &str) -> NodeId {
    let nid = NodeId::new(id);
    let info = arf_core::NodeInfo {
        node_id: nid.clone(),
        node_type: "test_target".into(),
        capabilities: serde_json::json!({"kind": "test_target"}),
        online_since: 0,
    };
    let filter = arf_core::MessageFilter {
        types: None,
        to_match: arf_core::ToMatch::BroadcastAndDirectedToMe,
    };
    let _handle = bus.connect(info, filter).await.expect("connect dummy");
    nid
}

/// Spawn a task that subscribes to the bus and counts peer_message receipts
/// (but NEVER sends a peer_reply — simulating a crashed/slow receiver).
fn spawn_no_reply_listener(bus: Arc<Bus>, count: Arc<AtomicUsize>) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut rx = bus.subscribe();
        let stop = tokio::time::Instant::now() + Duration::from_secs(5);
        while let Ok(recv) = tokio::time::timeout_at(stop, rx.recv()).await {
            let m = match recv {
                Ok(m) => m,
                Err(_) => break,
            };
            if m.msg_type == "peer_message" {
                count.fetch_add(1, Ordering::SeqCst);
                // intentionally NOT sending peer_reply — simulates B being down
            }
        }
    })
}

// [E2E] crash → restart → 同一 store → resend 自动重发 + attempt 累计
#[tokio::test(flavor = "multi_thread")]
async fn crash_then_restart_resends_pending() {
    let tmp = tempfile::tempdir().unwrap();
    let store = Arc::new(JsonlSessionStore::new(tmp.path()));

    let cid = uuid::Uuid::new_v4();

    // ── Phase 1: process-1 — bus1 + engine A + target B (no reply) ──
    let b1_count = Arc::new(AtomicUsize::new(0));
    {
        let bus1 = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
        let _ma = ModelAdapterNode::new(
            noop_provider("np1") as Arc<dyn Provider>,
            &bus1,
            NodeId::new("model/np1"),
        )
        .await
        .unwrap();
        let _b_target = register_target(&bus1, "B").await;
        let _listener = spawn_no_reply_listener(bus1.clone(), b1_count.clone());
        tokio::time::sleep(Duration::from_millis(100)).await;

        let engine_a = EngineBuilder::new(vec![bus1.clone()])
            .with_session_store(store.clone() as Arc<dyn SessionStore>)
            .with_agent_id(NodeId::new("A"))
            .with_session_id("A-sess")
            .build(make_cfg("np1"))
            .await
            .unwrap();

        let pm = arf_core::PeerMessage::new("A-sess", "B-sess", "hello");
        let payload = serde_json::to_value(&pm).unwrap();
        engine_a
            .handle()
            .send_message(Message::new(
                "peer_message",
                NodeId::new("A"),
                vec![NodeId::new("B")],
                payload,
            ))
            .await
            .expect("send peer_message");

        // 模拟 Engine 主循环的 peer_message_sent 钩子
        let sent = PendingPeerMessage {
            correlation_id: cid,
            target_session: "B-sess".into(),
            target_node: "B".into(),
            payload: serde_json::json!({
                "correlation_id": cid.to_string(),
                "from_session": "A-sess",
                "to_session": "B-sess",
                "content": "hello",
            }),
            sent_at: chrono::Utc::now(),
            attempt: 1,
        };
        store.record_peer_message_sent("A-sess", &sent).await.unwrap();

        tokio::time::sleep(Duration::from_millis(200)).await;
        assert_eq!(
            b1_count.load(Ordering::SeqCst),
            1,
            "B 应收到首发 1 次"
        );
        // engine_a 离开作用域 → bus1 后续被 drop（process crash 模拟）
    }

    // 验证 store 有 1 条 pending
    let pending = store.pending_peer_messages("A-sess").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].correlation_id, cid);
    assert_eq!(pending[0].attempt, 1);

    // ── Phase 2: process-2 — 新 bus2，新 target B，engine A 重启 ──
    let b2_count = Arc::new(AtomicUsize::new(0));
    {
        let bus2 = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
        let _ma = ModelAdapterNode::new(
            noop_provider("np2") as Arc<dyn Provider>,
            &bus2,
            NodeId::new("model/np2"),
        )
        .await
        .unwrap();
        let _b_target = register_target(&bus2, "B").await;
        let _listener = spawn_no_reply_listener(bus2.clone(), b2_count.clone());
        tokio::time::sleep(Duration::from_millis(100)).await;

        let _engine_a2 = EngineBuilder::new(vec![bus2.clone()])
            .with_session_store(store.clone() as Arc<dyn SessionStore>)
            .with_agent_id(NodeId::new("A"))
            .with_session_id("A-sess")
            .build(make_cfg("np2"))
            .await
            .unwrap();
        // build() 末尾自动 resend

        tokio::time::sleep(Duration::from_millis(300)).await;
        assert_eq!(
            b2_count.load(Ordering::SeqCst),
            1,
            "新进程 B 应收到 resend 1 次"
        );
    }

    // Store：attempt=2 的新 event 已写
    let pending2 = store.pending_peer_messages("A-sess").await.unwrap();
    assert_eq!(pending2.len(), 1, "B 未回 reply，故仍 pending");
    assert_eq!(pending2[0].correlation_id, cid);
    assert_eq!(pending2[0].attempt, 2, "resend 应写 attempt=2");
}

// [E2E] 已收 reply 的不重发
#[tokio::test(flavor = "multi_thread")]
async fn restart_does_not_resend_completed() {
    let tmp = tempfile::tempdir().unwrap();
    let store = Arc::new(JsonlSessionStore::new(tmp.path()));

    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let _ma = ModelAdapterNode::new(
        noop_provider("np2") as Arc<dyn Provider>,
        &bus,
        NodeId::new("model/np2"),
    )
    .await
    .unwrap();
    let _b_target = register_target(&bus, "B2").await;

    // Listener: only counts peer_message (no reply).
    let b_count = Arc::new(AtomicUsize::new(0));
    let listener = spawn_no_reply_listener(bus.clone(), b_count.clone());
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Pre-seed: sent + reply 都已经记录（模拟完整往返已完成）
    let cid = uuid::Uuid::new_v4();
    let sent = PendingPeerMessage {
        correlation_id: cid,
        target_session: "B2-sess".into(),
        target_node: "B2".into(),
        payload: serde_json::json!({"content": "x"}),
        sent_at: chrono::Utc::now(),
        attempt: 1,
    };
    store.record_peer_message_sent("A2-sess", &sent).await.unwrap();
    store
        .record_peer_reply_received("A2-sess", cid, "B2")
        .await
        .unwrap();

    let pending = store.pending_peer_messages("A2-sess").await.unwrap();
    assert!(pending.is_empty(), "已收 reply 不应 pending");

    // 重启 A — build() 时 resend 不应发任何东西
    let _engine_a2 = EngineBuilder::new(vec![bus.clone()])
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .with_agent_id(NodeId::new("A2"))
        .with_session_id("A2-sess")
        .build(make_cfg("np2"))
        .await
        .unwrap();

    tokio::time::sleep(Duration::from_millis(400)).await;
    assert_eq!(
        b_count.load(Ordering::SeqCst),
        0,
        "已完成的 cid 不应 resend"
    );

    listener.abort();
}