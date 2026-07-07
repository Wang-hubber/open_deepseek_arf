//! multi_agent_peer_and_subagent.rs — Phase 9 task 9.9.7
//!
//! 探查：3+ agent + peer + subagent 同时。3 engine 同时支持 peer 通信 + subagent 委派。
//!
//! **Framework 现状**（沿 9.9.6 + 9.9.5）：
//! - peer_message / peer_reply / subagent_delegate / subagent_result 都是 ActionMessage
//! - engine_response_types 累加多个 route key → response type
//! - 同一 engine 可同时注册 peer + subagent handler
//! - F-010 + F-011 + F-012 沿用
//!
//! **测试设计**（2 test cases）：
//! 1. `three_engines_with_both_peer_and_subagent_routes` — 3 engine routes 同时含两种协议
//! 2. `three_engines_peer_and_subagent_independent` — 1 peer + 1 subagent 同时发，独立处理
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.9.7.md`

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{
    Message, ModelMessage, NodeId, PeerMessage, PeerReply, Route, SubagentDelegate,
    SubagentResult, SubagentStatus,
};
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

// ── PeerEchoHandler — 收 peer_message 回 peer_reply ─────────────────────

struct PeerEchoHandler {
    my_engine_id: NodeId,
    echo_prefix: String,
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

// ── SubagentHandler — 收 subagent_delegate 回 subagent_result ────────────

struct SubagentHandler {
    my_engine_id: NodeId,
    call_count: Arc<std::sync::atomic::AtomicUsize>,
}

impl SubagentHandler {
    fn new(my_engine_id: NodeId) -> Self {
        Self {
            my_engine_id,
            call_count: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
        }
    }
}

impl MessageHandler for SubagentHandler {
    fn msg_type(&self) -> &'static str { "subagent_delegate" }
    fn handle(&self, ctx: &HandlerContext, msg: Message) -> Result<HandlerOutcome, RunError> {
        self.call_count.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let sd: SubagentDelegate = serde_json::from_value(msg.payload.clone())
            .map_err(|e| RunError::Internal(format!("subagent_delegate parse: {e}")))?;
        let reply = SubagentResult {
            correlation_id: sd.correlation_id,
            status: SubagentStatus::Success,
            output: format!("[done] {}", sd.task),
            trajectory: vec![],
        };
        let reply_payload = serde_json::to_value(&reply).unwrap();
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
                    "subagent_result",
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
    // 关键：routes 同时含 peer_message + subagent_delegate
    routes.insert("peer_message".into(), Route::Strict(vec![]));
    routes.insert("subagent_delegate".into(), Route::Strict(vec![]));
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
        tools: vec![],
        engine: EngineConfig {
            routes,
            checkpoint_rules: vec![],
            processors: HashMap::new(),
            on_member_failed: None,
                middlewares: vec![],
            max_turns: 5,
            tool_timeout_ms: Some(3_000),
        inbound_dedup_capacity: 1024,
        },
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 1：3 engine routes 同时含 peer_message + subagent_delegate
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn three_engines_with_both_peer_and_subagent_routes() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // 3 model adapter
    let prov_a: Arc<dyn Provider> = Arc::new(SimpleMock { name: "na7".into(), model: "na7-v1".into(), text: "a-reply".into() });
    let prov_b: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nb7".into(), model: "nb7-v1".into(), text: "b-reply".into() });
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nc7".into(), model: "nc7-v1".into(), text: "c-reply".into() });
    let _ma = ModelAdapterNode::new(prov_a, &bus, NodeId::new("model/na7")).await.expect("ma");
    let _mb = ModelAdapterNode::new(prov_b, &bus, NodeId::new("model/nb7")).await.expect("mb");
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/nc7")).await.expect("mc");

    // 3 engine
    let engine_a = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("na7", "na7-v1")).await.expect("ea");
    let engine_b = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nb7", "nb7-v1")).await.expect("eb");
    let engine_c = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nc7", "nc7-v1")).await.expect("ec");

    // 验证 3 engine filter 同时含 peer_reply + subagent_result
    for (label, engine) in [("a", &engine_a), ("b", &engine_b), ("c", &engine_c)] {
        let types = engine.handle().filter_config().types.clone().expect("types");
        println!("[test1] engine_{label} filter types: {types:?}");
        assert!(types.contains(&"peer_reply".to_string()),
                "engine_{label} filter should contain peer_reply, got {types:?}");
        assert!(types.contains(&"subagent_result".to_string()),
                "engine_{label} filter should contain subagent_result, got {types:?}");
        assert!(types.contains(&"model_response".to_string()));
        assert!(types.contains(&"tool_result".to_string()));
    }

    // 验证 bus.send peer_message 定向
    let pm = PeerMessage::new(
        engine_a.session_id(),
        engine_b.session_id(),
        "ping",
    );
    let pm_payload = serde_json::to_value(&pm).unwrap();
    let r_peer = bus.send(Message::new(
        "peer_message",
        engine_a.agent_id().clone(),
        vec![engine_b.agent_id().clone()],
        pm_payload,
    )).await.expect("send peer");
    println!("[test1] peer_message send: online_nodes={}, matching_nodes={}", r_peer.online_nodes, r_peer.matching_nodes);
    assert!(r_peer.matching_nodes >= 1, "peer should reach target");

    // 验证 bus.send subagent_delegate 定向
    let sd = SubagentDelegate::new(
        engine_a.session_id(),
        engine_c.agent_id().clone(),
        "do something",
    );
    let sd_payload = serde_json::to_value(&sd).unwrap();
    let r_sub = bus.send(Message::new(
        "subagent_delegate",
        engine_a.agent_id().clone(),
        vec![engine_c.agent_id().clone()],
        sd_payload,
    )).await.expect("send subagent");
    println!("[test1] subagent_delegate send: online_nodes={}, matching_nodes={}", r_sub.online_nodes, r_sub.matching_nodes);
    assert!(r_sub.matching_nodes >= 1, "subagent should reach target");
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 2：1 peer + 1 subagent 同时发，独立 handler 处理，6 个 reply 互不干扰
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn three_engines_peer_and_subagent_independent() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // 3 model adapter
    let prov_a: Arc<dyn Provider> = Arc::new(SimpleMock { name: "na7b".into(), model: "na7b-v1".into(), text: "a-reply".into() });
    let prov_b: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nb7b".into(), model: "nb7b-v1".into(), text: "b-reply".into() });
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nc7b".into(), model: "nc7b-v1".into(), text: "c-reply".into() });
    let _ma = ModelAdapterNode::new(prov_a, &bus, NodeId::new("model/na7b")).await.expect("ma");
    let _mb = ModelAdapterNode::new(prov_b, &bus, NodeId::new("model/nb7b")).await.expect("mb");
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/nc7b")).await.expect("mc");

    // 3 engine
    let engine_a = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("na7b", "na7b-v1")).await.expect("ea");
    let mut engine_b = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nb7b", "nb7b-v1")).await.expect("eb");
    let mut engine_c = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nc7b", "nc7b-v1")).await.expect("ec");

    // engine_a 仅发，不注册 handler
    // engine_b 注册 PeerEchoHandler（收 peer_message）
    let peer_handler_b = PeerEchoHandler::new(engine_b.agent_id().clone(), "[b-peer]");
    let peer_count_b = peer_handler_b.call_count.clone();
    // engine_c 注册 SubagentHandler（收 subagent_delegate）
    let sub_handler_c = SubagentHandler::new(engine_c.agent_id().clone());
    let sub_count_c = sub_handler_c.call_count.clone();

    tokio::task::block_in_place(|| {
        engine_b.add_handler(Arc::new(peer_handler_b), true);
        engine_c.add_handler(Arc::new(sub_handler_c), true);
    });

    // 启动 reply_watcher：监听 peer_reply + subagent_result
    let bus_rw = bus.clone();
    let reply_watcher = tokio::spawn(async move {
        let mut rx = bus_rw.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(5);
        let mut peer_replies = Vec::new();
        let mut subagent_results = Vec::new();
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "peer_reply" {
                peer_replies.push(m);
            } else if m.msg_type == "subagent_result" {
                subagent_results.push(m);
            }
            if peer_replies.len() >= 1 && subagent_results.len() >= 1 {
                break;
            }
        }
        (peer_replies, subagent_results)
    });
    tokio::time::sleep(Duration::from_millis(100)).await;

    // 启动 peer listener：A→B
    let bus_p = bus.clone();
    let engine_b_id = engine_b.agent_id().clone();
    let peer_listener = tokio::spawn(async move {
        let mut rx = bus_p.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(3);
        let mut found = None;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "peer_message" && m.to.contains(&engine_b_id) {
                found = Some(m);
                break;
            }
        }
        found
    });
    tokio::time::sleep(Duration::from_millis(80)).await;

    // 启动 subagent listener：A→C
    let bus_s = bus.clone();
    let engine_c_id = engine_c.agent_id().clone();
    let sub_listener = tokio::spawn(async move {
        let mut rx = bus_s.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(3);
        let mut found = None;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "subagent_delegate" && m.to.contains(&engine_c_id) {
                found = Some(m);
                break;
            }
        }
        found
    });
    tokio::time::sleep(Duration::from_millis(80)).await;

    // 构造 peer_message A→B
    let pm = PeerMessage::new(
        engine_a.session_id(),
        engine_b.session_id(),
        "hello from a",
    );
    let peer_cid = pm.correlation_id;
    let pm_payload = serde_json::to_value(&pm).unwrap();

    // 构造 subagent_delegate A→C
    let sd = SubagentDelegate::new(
        engine_a.session_id(),
        engine_c.agent_id().clone(),
        "task from a",
    );
    let sub_cid = sd.correlation_id;
    let sd_payload = serde_json::to_value(&sd).unwrap();

    // 并发 send（互不干扰）
    let r1 = bus.send(Message::new(
        "peer_message",
        engine_a.agent_id().clone(),
        vec![engine_b.agent_id().clone()],
        pm_payload,
    ));
    let r2 = bus.send(Message::new(
        "subagent_delegate",
        engine_a.agent_id().clone(),
        vec![engine_c.agent_id().clone()],
        sd_payload,
    ));
    let (r_peer, r_sub) = tokio::join!(r1, r2);
    println!("[test2] peer send: {:?}", r_peer.as_ref().map(|r| (r.online_nodes, r.matching_nodes)));
    println!("[test2] sub send: {:?}", r_sub.as_ref().map(|r| (r.online_nodes, r.matching_nodes)));
    assert!(r_peer.is_ok() && r_sub.is_ok(), "both sends should succeed");

    // 收 listener + dispatch
    let found_peer = peer_listener.await.expect("peer_listener").expect("should see peer");
    let found_sub = sub_listener.await.expect("sub_listener").expect("should see sub");

    let dispatch_peer = tokio::task::block_in_place(|| engine_b.dispatch_incoming(found_peer));
    let dispatch_sub = tokio::task::block_in_place(|| engine_c.dispatch_incoming(found_sub));
    println!("[test2] peer dispatch: {dispatch_peer:?}");
    println!("[test2] sub dispatch: {dispatch_sub:?}");

    // 等 reply_watcher 收 1 peer_reply + 1 subagent_result
    let (peer_replies, subagent_results) = reply_watcher.await.expect("reply_watcher");
    println!("[test2] received {} peer_reply, {} subagent_result", peer_replies.len(), subagent_results.len());
    assert_eq!(peer_replies.len(), 1, "should have 1 peer_reply");
    assert_eq!(subagent_results.len(), 1, "should have 1 subagent_result");

    // 验证 peer_reply
    let peer_reply: PeerReply = serde_json::from_value(peer_replies[0].payload.clone()).unwrap();
    println!("[test2] peer_reply: cid={}, content={}, to={:?}",
             peer_reply.correlation_id, peer_reply.content, peer_replies[0].to);
    assert_eq!(peer_reply.correlation_id, peer_cid, "peer_reply cid should match");
    assert!(peer_reply.content.contains("b-peer"), "peer_reply should be from engine_b");
    assert!(peer_replies[0].to.contains(&engine_a.agent_id().clone().into()),
            "peer_reply to should be engine_a");

    // 验证 subagent_result
    let sub_result: SubagentResult = serde_json::from_value(subagent_results[0].payload.clone()).unwrap();
    println!("[test2] subagent_result: cid={}, output={}, to={:?}",
             sub_result.correlation_id, sub_result.output, subagent_results[0].to);
    assert_eq!(sub_result.correlation_id, sub_cid, "subagent_result cid should match");
    assert_eq!(sub_result.status, SubagentStatus::Success);
    assert!(sub_result.output.contains("done"), "sub_result should be from handler");
    assert!(subagent_results[0].to.contains(&engine_a.agent_id().clone().into()),
            "subagent_result to should be engine_a");

    // 验证 2 个 handler 各被调 1 次
    assert_eq!(peer_count_b.load(std::sync::atomic::Ordering::SeqCst), 1,
               "engine_b PeerEchoHandler should fire once");
    assert_eq!(sub_count_c.load(std::sync::atomic::Ordering::SeqCst), 1,
               "engine_c SubagentHandler should fire once");
}
