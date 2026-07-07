//! nested_subagent_two_layer.rs — Phase 9 task 9.9.4
//!
//! 探查：双 agent + subagent 嵌套（2 层）。parent → child → grandchild。
//!
//! **Framework 现状**（沿 9.9.3）：
//! - SubagentDelegate 是纯数据协议
//! - handler 派发需 app 桥接（沿 F-011）
//! - correlation_id 须端到端匹配
//! - 同 bus 多 engine 受 F-010（agent_id 命名）限制，每个 engine 需唯一 provider
//!
//! **测试设计**（2 test cases）：
//! 1. `nested_two_layer_chain_constructed` — 3 engine + 2 SubagentDelegate，验证链能搭
//! 2. `nested_two_layer_correlation_id_propagates` — 2 层委派 cid 端到端匹配，grandchild output 透传 parent
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.9.4.md`
//! 预期：F-010 + F-011 沿用，可能 1 新 lesion（中间层转发缺失）

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

// ── SubagentLeafHandler — 终层（grandchild）只回 SubagentResult ──────────

struct LeafHandler {
    /// 真实 online NodeId：grandchild 的 agent_id（send 验证 to 必须 online）
    leaf_node_id: NodeId,
    /// 中间层（child）真实 online agent_id：result 应送回 child
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

// ── SubagentForwardHandler — 中间层（child）转发给 grandchild ────────────

struct ForwardHandler {
    /// grandchild engine id
    grandchild_id: NodeId,
    /// 转发次数
    forward_count: Arc<std::sync::atomic::AtomicUsize>,
}

impl MessageHandler for ForwardHandler {
    fn msg_type(&self) -> &'static str { "subagent_delegate" }
    fn handle(&self, ctx: &HandlerContext, msg: Message) -> Result<HandlerOutcome, RunError> {
        let sd: SubagentDelegate = serde_json::from_value(msg.payload.clone())
            .map_err(|e| RunError::Internal(format!("parse: {e}")))?;
        self.forward_count.fetch_add(1, std::sync::atomic::Ordering::SeqCst);

        // 转发：构造新的 SubagentDelegate 发给 grandchild
        // 关键：保留 correlation_id（parent 用的同一个 cid 要透传）
        let forwarded = SubagentDelegate::new(
            sd.parent_session_id.clone(),
            self.grandchild_id.clone(),
            format!("[forwarded] {}", sd.task),
        ).with_context(sd.context.clone());
        // 关键：必须用原 correlation_id，不能让 SubagentDelegate::new 自己 new 一个
        let mut fwd = forwarded;
        fwd.correlation_id = sd.correlation_id;
        let fwd_payload = serde_json::to_value(&fwd).unwrap();
        let bus = ctx.bus.clone();
        let grandchild_id = self.grandchild_id.clone();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("build rt");
            let _ = rt.block_on(async move {
                let _ = bus.send(Message::new(
                    "subagent_delegate",
                    NodeId::new("engine/child-stub"),
                    vec![grandchild_id],
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
// Test 1：3 engine + 2 SubagentDelegate 链能搭
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn nested_two_layer_chain_constructed() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // 3 个 model adapter
    let prov_p: Arc<dyn Provider> = Arc::new(SimpleMock { name: "np".into(), model: "np-v1".into(), text: "p-reply".into() });
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nc".into(), model: "nc-v1".into(), text: "c-reply".into() });
    let prov_g: Arc<dyn Provider> = Arc::new(SimpleMock { name: "ng".into(), model: "ng-v1".into(), text: "g-reply".into() });
    let _mp = ModelAdapterNode::new(prov_p, &bus, NodeId::new("model/np")).await.expect("mp");
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/nc")).await.expect("mc");
    let _mg = ModelAdapterNode::new(prov_g, &bus, NodeId::new("model/ng")).await.expect("mg");

    // 3 engine（用不同 provider 名避开 F-010）
    let engine_p = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("np", "np-v1")).await.expect("ep");
    let engine_c = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nc", "nc-v1")).await.expect("ec");
    let engine_g = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("ng", "ng-v1")).await.expect("eg");

    // 验证 3 engine 都在 bus 上
    let g = bus.graph();
    let n_engine = g.nodes.iter().filter(|n| n.node_type == "engine").count();
    println!("[test1] online engines: {n_engine}");
    assert_eq!(n_engine, 3, "expected 3 engines online, got {n_engine}");

    // 构造 2 层 SubagentDelegate
    // L1: parent → child
    let sd1 = SubagentDelegate::new(
        engine_p.session_id(),
        engine_c.agent_id().clone(),
        "L1 task: do X",
    );
    // L2: child → grandchild（中间层转发）
    let sd2 = SubagentDelegate::new(
        engine_p.session_id(),
        engine_g.agent_id().clone(),
        "L2 task: do Y",
    );
    let cid1 = sd1.correlation_id;
    let cid2 = sd2.correlation_id;
    println!("[test1] L1 cid={cid1}");
    println!("[test1] L2 cid={cid2}");
    assert_ne!(cid1, cid2, "L1 and L2 should have different cids unless explicitly forwarded");

    // bus.send L1 → child
    let sd1_payload = serde_json::to_value(&sd1).unwrap();
    let r1 = bus.send(Message::new(
        "subagent_delegate",
        engine_p.agent_id().clone(),
        vec![engine_c.agent_id().clone()],
        sd1_payload,
    )).await.expect("send L1");
    println!("[test1] L1 send: online_nodes={}, matching_nodes={}", r1.online_nodes, r1.matching_nodes);
    assert!(r1.matching_nodes >= 1, "L1 should reach child");

    // bus.send L2 → grandchild
    let sd2_payload = serde_json::to_value(&sd2).unwrap();
    let r2 = bus.send(Message::new(
        "subagent_delegate",
        engine_c.agent_id().clone(),
        vec![engine_g.agent_id().clone()],
        sd2_payload,
    )).await.expect("send L2");
    println!("[test1] L2 send: online_nodes={}, matching_nodes={}", r2.online_nodes, r2.matching_nodes);
    assert!(r2.matching_nodes >= 1, "L2 should reach grandchild");
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 2：2 层委派 correlation_id 端到端匹配，grandchild output 透传 parent
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn nested_two_layer_correlation_id_propagates() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    // 3 model adapter
    let prov_p: Arc<dyn Provider> = Arc::new(SimpleMock { name: "np2".into(), model: "np2-v1".into(), text: "p-reply".into() });
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock { name: "nc2".into(), model: "nc2-v1".into(), text: "c-reply".into() });
    let prov_g: Arc<dyn Provider> = Arc::new(SimpleMock { name: "ng2".into(), model: "ng2-v1".into(), text: "g-reply".into() });
    let _mp = ModelAdapterNode::new(prov_p, &bus, NodeId::new("model/np2")).await.expect("mp");
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/nc2")).await.expect("mc");
    let _mg = ModelAdapterNode::new(prov_g, &bus, NodeId::new("model/ng2")).await.expect("mg");

    // 3 engine
    let engine_p = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("np2", "np2-v1")).await.expect("ep");
    let mut engine_c = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("nc2", "nc2-v1")).await.expect("ec");
    let mut engine_g = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("ng2", "ng2-v1")).await.expect("eg");

    // child engine_c 注册：ForwardHandler（收到 subagent_delegate 转发给 grandchild）
    let forward_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let forward_handler = ForwardHandler {
        grandchild_id: engine_g.agent_id().clone(),
        forward_count: forward_count.clone(),
    };
    // grandchild engine_g 注册：LeafHandler（终层）
    let leaf_handler = LeafHandler {
        leaf_node_id: engine_g.agent_id().clone(),
        reply_to: engine_c.agent_id().clone(),
    };

    tokio::task::block_in_place(|| {
        engine_c.add_handler(Arc::new(forward_handler), true);
        engine_g.add_handler(Arc::new(leaf_handler), true);
    });

    // 启动 listener 收 L1 subagent_delegate → dispatch 到 child
    let bus_l1 = bus.clone();
    let engine_c_id = engine_c.agent_id().clone();
    let l1_listener = tokio::spawn(async move {
        let mut rx = bus_l1.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(2);
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
    println!("[test2] L1 dispatched to child, forward_count={}", forward_count.load(std::sync::atomic::Ordering::SeqCst));

    // 启动 L2 listener：收 child 转发的 subagent_delegate → dispatch 到 grandchild
    let bus_l2 = bus.clone();
    let engine_g_id = engine_g.agent_id().clone();
    let l2_listener = tokio::spawn(async move {
        let mut rx = bus_l2.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(2);
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

    let found_l2 = l2_listener.await.expect("l2_listener").expect("should see L2");
    // 验证 L2 的 correlation_id = L1 的 cid（中间层透传）
    let parsed_l2: SubagentDelegate = serde_json::from_value(found_l2.payload.clone()).unwrap();
    println!("[test2] L2 forwarded task={}, cid={}", parsed_l2.task, parsed_l2.correlation_id);
    assert_eq!(parsed_l2.correlation_id, cid_root, "L2 must reuse L1's cid for end-to-end tracking");
    assert!(parsed_l2.task.contains("forwarded"), "L2 task should be forwarded wrapper");

    // 启动 leaf_result_watcher：监听 grandchild → child 的 subagent_result
    // 关键：先 subscribe 再 dispatch，否则 LeafHandler 内 std::thread::spawn 的 send 会先于 subscribe
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
    // 等 leaf_result_watcher subscribe 稳定
    tokio::time::sleep(Duration::from_millis(200)).await;

    tokio::task::block_in_place(|| {
        let _ = engine_g.dispatch_incoming(found_l2);
    });
    println!("[test2] L2 dispatched to grandchild, forward_count={}",
             forward_count.load(std::sync::atomic::Ordering::SeqCst));

    // 等 leaf_result_watcher 收 grandchild → child 的 subagent_result
    let reply_msg = leaf_result_watcher.await.expect("leaf_result_watcher")
        .expect("should have seen subagent_result from grandchild");
    let parsed: SubagentResult = serde_json::from_value(reply_msg.payload).unwrap();
    println!("[test2] child 收到 leaf subagent_result: output={}, status={:?}, cid={}",
             parsed.output, parsed.status, parsed.correlation_id);
    assert_eq!(parsed.correlation_id, cid_root, "leaf result cid must match root");
    assert_eq!(parsed.status, SubagentStatus::Success);
    assert!(parsed.output.contains("leaf"), "output should originate from leaf: {}", parsed.output);
    assert!(parsed.output.contains("forwarded"), "output should be wrapped by forwarder: {}", parsed.output);

    // 验证中间层（child）的 forward_count 已被触发 1 次
    assert_eq!(forward_count.load(std::sync::atomic::Ordering::SeqCst), 1,
               "ForwardHandler should fire once on L1");
}
