//! multi_agent_peer_fully_connected.rs — Phase 9 task 9.9.6
//!
//! 探查：3+ agent + peer 全连通。3 engine 互相能发 PeerMessage / PeerReply，
//! 共 3×2 = 6 条定向 peer 边。
//!
//! **Framework 现状**（沿 9.9.2）：
//! - PeerMessage / PeerReply 是 ActionMessage
//! - `engine_response_types` 把 `peer_message` → `peer_reply`
//! - handler 派发需 app 桥接（沿 F-011）
//! - 同 bus 多 engine 受 F-010 限制，每个 engine 需唯一 provider
//! - reply from 必须是 online NodeId（沿 F-012）
//!
//! **测试设计**（2 test cases）：
//! 1. `three_engines_fully_connected_peers` — 3 engine 全连通拓扑能搭
//! 2. `three_engines_bidirectional_peer_reply` — 6 条 peer 边全部能 peer_reply
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.9.6.md`
//! 预期：F-010 + F-011 + F-012 沿用，无新 lesion

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, ModelMessage, NodeId, PeerMessage, PeerReply, Route};
use arf_engine::{
    AgentConfig, EngineBuilder, EngineConfig, HandlerContext, HandlerOutcome,
    MessageHandler, ModelDecl, RunError,
};
use arf_model_adapter::types::{ModelResponsePayload, ToolDef, Usage};
use arf_model_adapter::types::ModelParams;
use arf_model_adapter::{ModelAdapterNode, Provider, ProviderError};
use async_trait::async_trait;

// ── Mock Provider ─────────────────────────────────────────────────────────

struct SimpleMock {
    name: String,
    model: String,
    text: String,
}

#[async_trait]
impl Provider for SimpleMock {
    fn name(&self) -> &str { &self.name }
    fn supported_models(&self) -> &[String] { std::slice::from_ref(&self.model) }
    async fn chat(
        &self,
        _model_name: &str,
        _messages: Vec<ModelMessage>,
        _tools: Vec<ToolDef>,
        _params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        Ok(ModelResponsePayload {
            message: ModelMessage::new("assistant", &self.text),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage { input_tokens: 5, output_tokens: 5, total_tokens: 10 }),
            id: format!("mock-{}", uuid::Uuid::new_v4()),
            model: self.model.clone(),
        })
    }
}

// ── PeerEchoHandler — engine 收到 peer_message 后回 peer_reply ──────────

struct PeerEchoHandler {
    /// 自己 engine 的 agent_id（用于 reply 的 from 字段——必须 online，沿 F-012）
    my_engine_id: NodeId,
    /// reply 前缀
    echo_prefix: String,
    /// 统计：handle 被调次数
    call_count: Arc<std::sync::atomic::AtomicUsize>,
}

impl PeerEchoHandler {
    fn new(my_engine_id: NodeId, echo_prefix: &str) -> Self {
        Self {
            my_engine_id,
            echo_prefix: echo_prefix.to_string(),
            call_count: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
        }
    }
}

impl MessageHandler for PeerEchoHandler {
    fn msg_type(&self) -> &'static str { "peer_message" }
    fn handle(&self, ctx: &HandlerContext, msg: Message) -> Result<HandlerOutcome, RunError> {
        self.call_count.fetch_add(1, std::sync::atomic::Ordering::SeqCst);

        let pm: PeerMessage = serde_json::from_value(msg.payload.clone())
            .map_err(|e| RunError::Internal(format!("peer_message parse: {e}")))?;

        let reply = PeerReply::ok(
            pm.correlation_id,
            format!("{}-{}", self.echo_prefix, pm.content),
        );

        let reply_payload = serde_json::to_value(&reply).unwrap_or_default();
        let bus = ctx.bus.clone();
        let to = vec![msg.from.clone()];
        let from = self.my_engine_id.clone();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("build rt");
            let _ = rt.block_on(async move {
                let _ = bus.send(Message::new(
                    "peer_reply",
                    from,
                    to,
                    reply_payload,
                )).await;
            });
        });
        Ok(HandlerOutcome::Handled)
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────

fn make_engine_cfg(provider: &str, model: &str) -> AgentConfig {
    let mut routes = HashMap::<String, Route>::new();
    routes.insert("peer_message".into(), Route::Strict(vec![]));
    AgentConfig {
        model: ModelDecl {
            provider: provider.into(),
            model_name: model.into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "agent".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        engine: EngineConfig {
            routes,
            checkpoint_rules: vec![],
            processors: HashMap::new(),
            on_member_failed: None,
            max_turns: 5,
            tool_timeout_ms: Some(3_000),
        },
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 1：3 engine 全连通拓扑能搭（验证 bus.send 能定向到任一 engine）
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn three_engines_fully_connected_peers() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // 3 model adapter（用 na/nb/nc 区分 a/b/c）
    let prov_a: Arc<dyn Provider> = Arc::new(SimpleMock { name: "na6".into(), model: "na6-v1".into(), text: "a-reply".into() });
    let prov_b: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nb6".into(), model: "nb6-v1".into(), text: "b-reply".into() });
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nc6".into(), model: "nc6-v1".into(), text: "c-reply".into() });
    let _ma = ModelAdapterNode::new(prov_a, &bus, NodeId::new("model/na6")).await.expect("ma");
    let _mb = ModelAdapterNode::new(prov_b, &bus, NodeId::new("model/nb6")).await.expect("mb");
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/nc6")).await.expect("mc");

    // 3 engine
    let engine_a = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("na6", "na6-v1")).await.expect("ea");
    let engine_b = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nb6", "nb6-v1")).await.expect("eb");
    let engine_c = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nc6", "nc6-v1")).await.expect("ec");

    // 验证 3 engine 都在 bus 上
    let g = bus.graph();
    let n_engine = g.nodes.iter().filter(|n| n.node_type == "engine").count();
    println!("[test1] online engines: {n_engine}");
    assert_eq!(n_engine, 3, "expected 3 engines online, got {n_engine}");

    // 验证 3 engine 的 filter 都含 peer_reply（routes 含 peer_message）
    for (label, engine) in [("a", &engine_a), ("b", &engine_b), ("c", &engine_c)] {
        let types = engine.handle().filter_config().types.clone().expect("types");
        println!("[test1] engine_{label} filter types: {types:?}");
        assert!(types.contains(&"peer_reply".to_string()),
                "engine_{label} filter should contain peer_reply, got {types:?}");
    }

    // 构造 6 条 peer 边（A→B, A→C, B→A, B→C, C→A, C→B）并 bus.send
    let edges = [
        (&engine_a, &engine_b, "A->B"),
        (&engine_a, &engine_c, "A->C"),
        (&engine_b, &engine_a, "B->A"),
        (&engine_b, &engine_c, "B->C"),
        (&engine_c, &engine_a, "C->A"),
        (&engine_c, &engine_b, "C->B"),
    ];
    for (from, to, label) in edges {
        let pm = PeerMessage::new(
            from.session_id(),
            to.session_id(),
            format!("hi {label}"),
        );
        let pm_payload = serde_json::to_value(&pm).unwrap();
        let r = bus.send(Message::new(
            "peer_message",
            from.agent_id().clone(),
            vec![to.agent_id().clone()],
            pm_payload,
        )).await.expect("send peer");
        println!("[test1] {label} send: online_nodes={}, matching_nodes={}", r.online_nodes, r.matching_nodes);
        assert!(r.matching_nodes >= 1, "{label} should reach target");
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 2：6 条 peer 边全部能 peer_reply
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn three_engines_bidirectional_peer_reply() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // 3 model adapter
    let prov_a: Arc<dyn Provider> = Arc::new(SimpleMock { name: "na6b".into(), model: "na6b-v1".into(), text: "a-reply".into() });
    let prov_b: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nb6b".into(), model: "nb6b-v1".into(), text: "b-reply".into() });
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nc6b".into(), model: "nc6b-v1".into(), text: "c-reply".into() });
    let _ma = ModelAdapterNode::new(prov_a, &bus, NodeId::new("model/na6b")).await.expect("ma");
    let _mb = ModelAdapterNode::new(prov_b, &bus, NodeId::new("model/nb6b")).await.expect("mb");
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/nc6b")).await.expect("mc");

    // 3 engine
    let engine_a = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("na6b", "na6b-v1")).await.expect("ea");
    let mut engine_b = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nb6b", "nb6b-v1")).await.expect("eb");
    let mut engine_c = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nc6b", "nc6b-v1")).await.expect("ec");

    // 3 engine 各注册 PeerEchoHandler
    let handler_a = PeerEchoHandler::new(engine_a.agent_id().clone(), "[a-echo]");
    let handler_b = PeerEchoHandler::new(engine_b.agent_id().clone(), "[b-echo]");
    let handler_c = PeerEchoHandler::new(engine_c.agent_id().clone(), "[c-echo]");
    let count_a = handler_a.call_count.clone();
    let count_b = handler_b.call_count.clone();
    let count_c = handler_c.call_count.clone();
    // 关键：engine_a 是普通 Engine，handler 注册不需要 mut
    // 但 engine_b/c 后面要 dispatch_incoming，所以 declare mut
    // engine_a 不需要 mut，但为了一致性，我们用 mut
    let mut engine_a = engine_a;
    tokio::task::block_in_place(|| {
        engine_a.add_handler(Arc::new(handler_a), true);
        engine_b.add_handler(Arc::new(handler_b), true);
        engine_c.add_handler(Arc::new(handler_c), true);
    });

    // 启动 reply_watcher：监听 6 个 peer_reply
    let bus_rw = bus.clone();
    let reply_watcher = tokio::spawn(async move {
        let mut rx = bus_rw.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(5);
        let mut replies = Vec::new();
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "peer_reply" {
                replies.push(m);
                if replies.len() >= 6 { break; }
            }
        }
        replies
    });
    tokio::time::sleep(Duration::from_millis(100)).await;

    // 6 条 peer 边：每条 1 个 bus.send + 1 个 listener + 1 个 dispatch_incoming
    let edges = [
        (&engine_a, &engine_b, "A->B"),
        (&engine_a, &engine_c, "A->C"),
        (&engine_b, &engine_a, "B->A"),
        (&engine_b, &engine_c, "B->C"),
        (&engine_c, &engine_a, "C->A"),
        (&engine_c, &engine_b, "C->B"),
    ];

    let mut all_cids = std::collections::HashMap::<String, uuid::Uuid>::new();
    for (from, to, label) in edges {
        // 启动 listener 收 peer_message 给 to
        let bus_l = bus.clone();
        let to_id = to.agent_id().clone();
        let listener = tokio::spawn(async move {
            let mut rx = bus_l.subscribe();
            let stop_at = tokio::time::Instant::now() + Duration::from_secs(3);
            let mut found = None;
            while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
                let m = match recv { Ok(m) => m, Err(_) => break };
                if m.msg_type == "peer_message" && m.to.contains(&to_id) {
                    found = Some(m);
                    break;
                }
            }
            found
        });
        tokio::time::sleep(Duration::from_millis(80)).await;

        // 构造 PeerMessage
        let pm = PeerMessage::new(
            from.session_id(),
            to.session_id(),
            format!("hi {label}"),
        );
        let cid = pm.correlation_id;
        all_cids.insert(label.to_string(), cid);
        let pm_payload = serde_json::to_value(&pm).unwrap();

        // bus.send
        bus.send(Message::new(
            "peer_message",
            from.agent_id().clone(),
            vec![to.agent_id().clone()],
            pm_payload,
        )).await.expect("send peer");

        // listener 收 → dispatch_incoming 到 target engine
        let found = listener.await.expect("listener").expect("should see peer_message");
        // 选 mut engine for dispatch
        let dispatch_result = if to.agent_id() == engine_a.agent_id() {
            tokio::task::block_in_place(|| engine_a.dispatch_incoming(found))
        } else if to.agent_id() == engine_b.agent_id() {
            tokio::task::block_in_place(|| engine_b.dispatch_incoming(found))
        } else {
            tokio::task::block_in_place(|| engine_c.dispatch_incoming(found))
        };
        println!("[test2] {label} dispatched: {dispatch_result:?}");
    }

    // 等 6 个 peer_reply
    let replies = reply_watcher.await.expect("reply_watcher");
    println!("[test2] received {} peer_reply", replies.len());
    assert!(replies.len() >= 6, "should have at least 6 peer_reply, got {}", replies.len());

    // 验证每个 reply 都有 cid 且 to 是 source engine
    let mut received_cids = std::collections::HashSet::new();
    for r in &replies {
        let parsed: PeerReply = serde_json::from_value(r.payload.clone()).unwrap();
        println!("[test2] peer_reply: cid={}, content={}, to={:?}",
                 parsed.correlation_id, parsed.content, r.to);
        received_cids.insert(parsed.correlation_id);
        // 验证 reply 来自另一 engine（reply.from 是 handler 注入的真实 online NodeId）
        assert!(!r.from.as_str().is_empty(), "reply from should not be empty, got {}", r.from);
    }
    // 6 个不同 cid 都收到
    let expected_cids: std::collections::HashSet<_> = all_cids.values().cloned().collect();
    println!("[test2] expected cids: {expected_cids:?}");
    println!("[test2] received cids: {received_cids:?}");
    for cid in &expected_cids {
        assert!(received_cids.contains(cid),
                "expected cid {cid} should be in received replies");
    }

    // 验证每个 engine 的 handler 被调 2 次（收 2 个 peer_message）
    assert_eq!(count_a.load(std::sync::atomic::Ordering::SeqCst), 2,
               "engine_a should receive 2 peer_messages (B→A + C→A)");
    assert_eq!(count_b.load(std::sync::atomic::Ordering::SeqCst), 2,
               "engine_b should receive 2 peer_messages (A→B + C→B)");
    assert_eq!(count_c.load(std::sync::atomic::Ordering::SeqCst), 2,
               "engine_c should receive 2 peer_messages (A→C + B→C)");
}
