//! nested_subagent_three_layer.rs — Phase 9 task 9.9.5
//!
//! 探查：3+ agent + subagent 嵌套（3 层）。parent → child → grandchild → great-grandchild。
//!
//! **Framework 现状**（沿 9.9.4）：
//! - SubagentDelegate 是纯数据协议
//! - handler 派发需 app 桥接（沿 F-011）
//! - correlation_id 须端到端匹配（4 engine 共享 1 个 cid_root）
//! - 同 bus 多 engine 受 F-010（agent_id 命名）限制，每个 engine 需唯一 provider
//! - handler 需注入真实 online NodeId（沿 F-012）
//!
//! **测试设计**（2 test cases）：
//! 1. `nested_three_layer_chain_constructed` — 4 engine + 3 SubagentDelegate，验证链能搭
//! 2. `nested_three_layer_correlation_id_propagates` — 3 层委派 cid 端到端匹配，great-grandchild output 透传 grandchild
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.9.5.md`
//! 预期：F-010 + F-011 + F-012 沿用，可能 1 新 lesion（3 层 N×F-012 扩大）

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, ModelMessage, NodeId, Route, SubagentDelegate, SubagentResult, SubagentStatus};
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

// ── LeafHandler — 终层（great-grandchild）只回 SubagentResult ──────────

struct LeafHandler {
    /// 真实 online NodeId：great-grandchild 的 agent_id
    leaf_node_id: NodeId,
    /// 中间层（grandchild）真实 online agent_id：result 应送回 grandchild
    reply_to: NodeId,
}

impl MessageHandler for LeafHandler {
    fn msg_type(&self) -> &'static str { "subagent_delegate" }
    fn handle(&self, ctx: &HandlerContext, msg: Message) -> Result<HandlerOutcome, RunError> {
        let sd: SubagentDelegate = serde_json::from_value(msg.payload.clone())
            .map_err(|e| RunError::Internal(format!("parse: {e}")))?;
        let reply = SubagentResult {
            correlation_id: sd.correlation_id,
            status: SubagentStatus::Success,
            output: format!("[leaf] {}", sd.task),
            trajectory: vec![],
        };
        let reply_payload = serde_json::to_value(&reply).unwrap();
        let bus = ctx.bus.clone();
        let leaf_node_id = self.leaf_node_id.clone();
        let reply_to = self.reply_to.clone();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("build rt");
            let _ = rt.block_on(async move {
                let _ = bus.send(Message::new(
                    "subagent_result",
                    leaf_node_id,
                    vec![reply_to],
                    reply_payload,
                )).await;
            });
        });
        Ok(HandlerOutcome::Handled)
    }
}

// ── ForwardHandler — 中间层（child / grandchild）转发给下一层 ────────────

struct ForwardHandler {
    /// 下一层 engine id
    next_id: NodeId,
    /// 转发次数
    forward_count: Arc<std::sync::atomic::AtomicUsize>,
}

impl MessageHandler for ForwardHandler {
    fn msg_type(&self) -> &'static str { "subagent_delegate" }
    fn handle(&self, ctx: &HandlerContext, msg: Message) -> Result<HandlerOutcome, RunError> {
        let sd: SubagentDelegate = serde_json::from_value(msg.payload.clone())
            .map_err(|e| RunError::Internal(format!("parse: {e}")))?;
        self.forward_count.fetch_add(1, std::sync::atomic::Ordering::SeqCst);

        // 转发：构造新的 SubagentDelegate 发给下一层
        // 关键：保留 correlation_id（parent 用的同一个 cid 要透传）
        let forwarded = SubagentDelegate::new(
            sd.parent_session_id.clone(),
            self.next_id.clone(),
            format!("[forwarded] {}", sd.task),
        ).with_context(sd.context.clone());
        // 关键：必须用原 correlation_id，不能让 SubagentDelegate::new 自己 new 一个
        let mut fwd = forwarded;
        fwd.correlation_id = sd.correlation_id;
        let fwd_payload = serde_json::to_value(&fwd).unwrap();
        let bus = ctx.bus.clone();
        let next_id = self.next_id.clone();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("build rt");
            let _ = rt.block_on(async move {
                let _ = bus.send(Message::new(
                    "subagent_delegate",
                    NodeId::new("engine/mid-stub"),
                    vec![next_id],
                    fwd_payload,
                )).await;
            });
        });
        Ok(HandlerOutcome::Handled)
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────

fn make_engine_cfg(provider: &str, model: &str) -> AgentConfig {
    let mut routes = HashMap::<String, Route>::new();
    routes.insert("subagent_delegate".into(), Route::Strict(vec![]));
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
        inbound_dedup_capacity: 1024,
        },
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 1：4 engine + 3 SubagentDelegate 链能搭
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn nested_three_layer_chain_constructed() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // 4 个 model adapter（用 p/c/g/gg 区分 parent/child/grandchild/great-grandchild）
    let prov_p: Arc<dyn Provider> = Arc::new(SimpleMock { name: "np3".into(), model: "np3-v1".into(), text: "p-reply".into() });
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nc3".into(), model: "nc3-v1".into(), text: "c-reply".into() });
    let prov_g: Arc<dyn Provider> = Arc::new(SimpleMock { name: "ng3".into(), model: "ng3-v1".into(), text: "g-reply".into() });
    let prov_gg: Arc<dyn Provider> = Arc::new(SimpleMock { name: "ngg3".into(), model: "ngg3-v1".into(), text: "gg-reply".into() });
    let _mp = ModelAdapterNode::new(prov_p, &bus, NodeId::new("model/np3")).await.expect("mp");
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/nc3")).await.expect("mc");
    let _mg = ModelAdapterNode::new(prov_g, &bus, NodeId::new("model/ng3")).await.expect("mg");
    let _mgg = ModelAdapterNode::new(prov_gg, &bus, NodeId::new("model/ngg3")).await.expect("mgg");

    // 4 engine（用不同 provider 名避开 F-010）
    let engine_p = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("np3", "np3-v1")).await.expect("ep");
    let engine_c = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nc3", "nc3-v1")).await.expect("ec");
    let engine_g = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("ng3", "ng3-v1")).await.expect("eg");
    let engine_gg = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("ngg3", "ngg3-v1")).await.expect("egg");

    // 验证 4 engine 都在 bus 上
    let g = bus.graph();
    let n_engine = g.nodes.iter().filter(|n| n.node_type == "engine").count();
    println!("[test1] online engines: {n_engine}");
    assert_eq!(n_engine, 4, "expected 4 engines online, got {n_engine}");

    // 构造 3 层 SubagentDelegate
    // L1: parent → child
    let sd1 = SubagentDelegate::new(
        engine_p.session_id(),
        engine_c.agent_id().clone(),
        "L1 task: do X",
    );
    // L2: child → grandchild
    let sd2 = SubagentDelegate::new(
        engine_p.session_id(),
        engine_g.agent_id().clone(),
        "L2 task: do Y",
    );
    // L3: grandchild → great-grandchild
    let sd3 = SubagentDelegate::new(
        engine_p.session_id(),
        engine_gg.agent_id().clone(),
        "L3 task: do Z",
    );
    let cid1 = sd1.correlation_id;
    let cid2 = sd2.correlation_id;
    let cid3 = sd3.correlation_id;
    println!("[test1] L1 cid={cid1}");
    println!("[test1] L2 cid={cid2}");
    println!("[test1] L3 cid={cid3}");
    assert_ne!(cid1, cid2, "L1 and L2 should have different cids");
    assert_ne!(cid1, cid3, "L1 and L3 should have different cids");
    assert_ne!(cid2, cid3, "L2 and L3 should have different cids");

    // 3 个 bus.send 验证定向
    for (label, sd, to_id) in [
        ("L1", &sd1, engine_c.agent_id().clone()),
        ("L2", &sd2, engine_g.agent_id().clone()),
        ("L3", &sd3, engine_gg.agent_id().clone()),
    ] {
        let payload = serde_json::to_value(sd).unwrap();
        let r = bus.send(Message::new(
            "subagent_delegate",
            engine_p.agent_id().clone(),
            vec![to_id],
            payload,
        )).await.expect("send");
        println!("[test1] {label} send: online_nodes={}, matching_nodes={}", r.online_nodes, r.matching_nodes);
        assert!(r.matching_nodes >= 1, "{label} should reach target");
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 2：3 层委派 correlation_id 端到端匹配，great-grandchild output 透传 grandchild
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn nested_three_layer_correlation_id_propagates() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // 4 个 model adapter
    let prov_p: Arc<dyn Provider> = Arc::new(SimpleMock { name: "np3b".into(), model: "np3b-v1".into(), text: "p-reply".into() });
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nc3b".into(), model: "nc3b-v1".into(), text: "c-reply".into() });
    let prov_g: Arc<dyn Provider> = Arc::new(SimpleMock { name: "ng3b".into(), model: "ng3b-v1".into(), text: "g-reply".into() });
    let prov_gg: Arc<dyn Provider> = Arc::new(SimpleMock { name: "ngg3b".into(), model: "ngg3b-v1".into(), text: "gg-reply".into() });
    let _mp = ModelAdapterNode::new(prov_p, &bus, NodeId::new("model/np3b")).await.expect("mp");
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/nc3b")).await.expect("mc");
    let _mg = ModelAdapterNode::new(prov_g, &bus, NodeId::new("model/ng3b")).await.expect("mg");
    let _mgg = ModelAdapterNode::new(prov_gg, &bus, NodeId::new("model/ngg3b")).await.expect("mgg");

    // 4 engine
    let engine_p = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("np3b", "np3b-v1")).await.expect("ep");
    let mut engine_c = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nc3b", "nc3b-v1")).await.expect("ec");
    let mut engine_g = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("ng3b", "ng3b-v1")).await.expect("eg");
    let mut engine_gg = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("ngg3b", "ngg3b-v1")).await.expect("egg");

    // child engine_c 注册：ForwardHandler（收到 subagent_delegate 转发给 grandchild）
    let fwd_c_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let fwd_c = ForwardHandler {
        next_id: engine_g.agent_id().clone(),
        forward_count: fwd_c_count.clone(),
    };
    // grandchild engine_g 注册：ForwardHandler（收到 subagent_delegate 转发给 great-grandchild）
    let fwd_g_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let fwd_g = ForwardHandler {
        next_id: engine_gg.agent_id().clone(),
        forward_count: fwd_g_count.clone(),
    };
    // great-grandchild engine_gg 注册：LeafHandler（终层）
    let leaf_handler = LeafHandler {
        leaf_node_id: engine_gg.agent_id().clone(),
        reply_to: engine_g.agent_id().clone(),
    };

    tokio::task::block_in_place(|| {
        engine_c.add_handler(Arc::new(fwd_c), true);
        engine_g.add_handler(Arc::new(fwd_g), true);
        engine_gg.add_handler(Arc::new(leaf_handler), true);
    });

    // 启动 L1 listener：收 subagent_delegate → dispatch 到 child
    let bus_l1 = bus.clone();
    let engine_c_id = engine_c.agent_id().clone();
    let l1_listener = tokio::spawn(async move {
        let mut rx = bus_l1.subscribe();
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
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Parent 发 L1 subagent_delegate
    let sd1 = SubagentDelegate::new(
        engine_p.session_id(),
        engine_c.agent_id().clone(),
        "task root",
    );
    let cid_root = sd1.correlation_id;
    let sd1_payload = serde_json::to_value(&sd1).unwrap();
    bus.send(Message::new(
        "subagent_delegate",
        engine_p.agent_id().clone(),
        vec![engine_c.agent_id().clone()],
        sd1_payload,
    )).await.expect("send L1");

    // Listener 收 L1 → dispatch 到 child engine_c
    let found_l1 = l1_listener.await.expect("l1_listener").expect("should see L1");
    tokio::task::block_in_place(|| {
        let _ = engine_c.dispatch_incoming(found_l1);
    });
    println!("[test2] L1 dispatched to child, fwd_c_count={}", fwd_c_count.load(std::sync::atomic::Ordering::SeqCst));

    // 启动 L2 listener：收 child 转发的 subagent_delegate → dispatch 到 grandchild
    let bus_l2 = bus.clone();
    let engine_g_id = engine_g.agent_id().clone();
    let l2_listener = tokio::spawn(async move {
        let mut rx = bus_l2.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(3);
        let mut found = None;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "subagent_delegate" && m.to.contains(&engine_g_id) {
                found = Some(m);
                break;
            }
        }
        found
    });
    tokio::time::sleep(Duration::from_millis(100)).await;

    let found_l2 = l2_listener.await.expect("l2_listener").expect("should see L2");
    // 验证 L2 的 correlation_id = L1 的 cid（中间层透传）
    let parsed_l2: SubagentDelegate = serde_json::from_value(found_l2.payload.clone()).unwrap();
    println!("[test2] L2 forwarded task={}, cid={}", parsed_l2.task, parsed_l2.correlation_id);
    assert_eq!(parsed_l2.correlation_id, cid_root, "L2 must reuse L1's cid");
    assert!(parsed_l2.task.contains("forwarded"), "L2 task should be forwarded wrapper");

    tokio::task::block_in_place(|| {
        let _ = engine_g.dispatch_incoming(found_l2);
    });
    println!("[test2] L2 dispatched to grandchild, fwd_g_count={}", fwd_g_count.load(std::sync::atomic::Ordering::SeqCst));

    // 启动 L3 listener：收 grandchild 转发的 subagent_delegate → dispatch 到 great-grandchild
    let bus_l3 = bus.clone();
    let engine_gg_id = engine_gg.agent_id().clone();
    let l3_listener = tokio::spawn(async move {
        let mut rx = bus_l3.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(3);
        let mut found = None;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "subagent_delegate" && m.to.contains(&engine_gg_id) {
                found = Some(m);
                break;
            }
        }
        found
    });
    tokio::time::sleep(Duration::from_millis(100)).await;

    let found_l3 = l3_listener.await.expect("l3_listener").expect("should see L3");
    // 验证 L3 的 correlation_id = L1 的 cid（中间层透传）
    let parsed_l3: SubagentDelegate = serde_json::from_value(found_l3.payload.clone()).unwrap();
    println!("[test2] L3 forwarded task={}, cid={}", parsed_l3.task, parsed_l3.correlation_id);
    assert_eq!(parsed_l3.correlation_id, cid_root, "L3 must reuse L1's cid");
    assert!(parsed_l3.task.contains("forwarded"), "L3 task should be forwarded wrapper");

    // 启动 leaf_result_watcher：在 dispatch L3 之前先 subscribe（避免漏消息）
    let bus_lr = bus.clone();
    let leaf_result_watcher = tokio::spawn(async move {
        let mut rx = bus_lr.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(5);
        let mut got = None;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "subagent_result" {
                got = Some(m);
                break;
            }
        }
        got
    });
    tokio::time::sleep(Duration::from_millis(200)).await;

    tokio::task::block_in_place(|| {
        let _ = engine_gg.dispatch_incoming(found_l3);
    });
    println!("[test2] L3 dispatched to great-grandchild");

    // 等 leaf_result_watcher 收 great-grandchild → grandchild 的 subagent_result
    let reply_msg = leaf_result_watcher.await.expect("leaf_result_watcher")
        .expect("should have seen subagent_result from great-grandchild");
    let parsed: SubagentResult = serde_json::from_value(reply_msg.payload).unwrap();
    println!("[test2] grandchild 收到 leaf subagent_result: output={}, status={:?}, cid={}",
             parsed.output, parsed.status, parsed.correlation_id);
    assert_eq!(parsed.correlation_id, cid_root, "leaf result cid must match root");
    assert_eq!(parsed.status, SubagentStatus::Success);
    assert!(parsed.output.contains("leaf"), "output should originate from leaf: {}", parsed.output);
    // task 应包含 2 层 forwarded（child→grandchild + grandchild→great-grandchild）
    assert!(parsed.output.matches("forwarded").count() == 2,
            "output should be forwarded 2 times: {}", parsed.output);

    // 验证 2 个中间层的 forward_count 都被触发 1 次
    assert_eq!(fwd_c_count.load(std::sync::atomic::Ordering::SeqCst), 1,
               "child ForwardHandler should fire once on L1");
    assert_eq!(fwd_g_count.load(std::sync::atomic::Ordering::SeqCst), 1,
               "grandchild ForwardHandler should fire once on L2");
}
