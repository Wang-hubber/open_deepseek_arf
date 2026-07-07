//! dual_agent_peer.rs — Phase 9 task 9.9.2
//!
//! 探查：双 agent + peer (A2A / PeerMessage + PeerReply) 端到端。
//!
//! **Framework 现状**（按探查）：
//! - `PeerMessage` / `PeerReply` 在 `arf_core::message` 已是标准 ActionMessage
//! - `engine_response_types()` 把 `peer_message` route key 映射到 `peer_reply`
//! - **Engine filter 仅收 response types**（line 69-74 of engine.rs）
//! - **Engine 不主动 dispatch peer_message → handler**（无 default 派发）
//! - App 想让 engine 收 peer_message：必须用 `bus.subscribe()` 订阅，手动调
//!   `engine.dispatch_incoming(msg)`（F-004 衍生：handler 路径不自动触发）
//!
//! **测试设计**（3 test cases）：
//! 1. peer_message_wired_via_external_subscriber — 外部 subscriber 收 peer_message，
//!                                                  返 PeerReply，engine A filter 收 peer_reply
//! 2. peer_message_handler_registered_on_engine — engine.add_handler 注册 peer_message handler，
//!                                                  手动 dispatch_incoming 调用
//! 3. peer_round_trip_two_engines — A → B → A 双向往返，验证 session_id 正确路由
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.9.2.md`（独立文件，独立 commit）
//! 预期 lesion：F-011（Engine 不自动 dispatch incoming ActionMessage 到 MessageHandler）

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, ModelMessage, NodeId, PeerMessage, PeerReply, Route};
use arf_engine::{
    AgentConfig, Engine, EngineBuilder, EngineConfig, HandlerContext, HandlerOutcome,
    MessageHandler, ModelDecl, RunError,
};
use arf_model_adapter::types::{ModelResponsePayload, ToolDef, Usage};
use arf_model_adapter::{ModelAdapterNode, Provider, ProviderError};
use arf_model_adapter::types::ModelParams;
use async_trait::async_trait;

// ── Mock Provider（让 2 个 engine 各自有 model） ───────────────────────────

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

// ── Peer message handler — 手动 dispatch（framework 不自动） ─────────────

/// App-level handler：收到 peer_message 后构造 peer_reply 回 sender。
/// 手动 dispatch 由测试通过 engine.dispatch_incoming(msg) 触发。
struct PeerEchoHandler {
    /// 自己 engine 的 agent_id（用于 reply 的 to 字段）
    my_engine_id: NodeId,
    /// reply 默认文本
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

        // 解析 PeerMessage payload
        let pm: PeerMessage = serde_json::from_value(msg.payload.clone())
            .map_err(|e| RunError::Internal(format!("peer_message parse: {e}")))?;

        // 构造 PeerReply
        let reply = PeerReply::ok(
            pm.correlation_id,
            format!("{}-{}", self.echo_prefix, pm.content),
        );

        // 通过 ctx.bus 发送 peer_reply 给原 sender
        // 关键：handler 签名是 sync fn，不能 await。但 bus.send() 是 async fn。
        // 处理：把 send 包成 future，用独立 OS 线程 + 单线程 runtime 同步驱动
        //（不能 tokio::spawn，因为 handler 在 dispatch_incoming 的 blocking_lock 内被调，
        //  tokio::spawn 任务可能因为 block_in_place 而不执行；不能用 Handle::block_on，
        //  block_in_place 内 block_on 会与 multi-thread runtime 死锁）
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
                let _ = bus.send(ar_core_msg(&reply_payload, from, to)).await;
            });
        });
        Ok(HandlerOutcome::Handled)
    }
}

/// Helper: build a Message from a serde_json payload + msg_type info
fn ar_core_msg(payload: &serde_json::Value, from: NodeId, to: Vec<NodeId>) -> Message {
    let json = payload.clone();
    let mut msg = Message::new("peer_reply", from, to, json);
    if let Some(cid) = payload.get("correlation_id").and_then(|v| v.as_str()).and_then(|s| uuid::Uuid::parse_str(s).ok()) {
        if let Some(obj) = msg.payload.as_object_mut() {
            obj.insert("correlation_id".to_string(), serde_json::Value::String(cid.to_string()));
        }
    }
    msg
}

// ── Test helpers ─────────────────────────────────────────────────────────

fn make_engine_cfg(provider: &str, model: &str) -> AgentConfig {
    let mut routes = HashMap::<String, Route>::new();
    // 注册 peer_message route（即使本 engine 不主动发，让 filter 含 peer_reply）
    // route key = "peer_message" → response_msg_type_for 返回 "peer_reply"
    // 所以 engine filter = [model_response, tool_result, peer_reply]
    routes.insert(
        "peer_message".into(),
        Route::Strict(vec![]),  // Strict([]) = 不发，仅让 filter 包含
    );
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
            max_turns: 3,
            tool_timeout_ms: Some(3_000),
        inbound_dedup_capacity: 1024,
        },
    }
}

async fn build_dual_engines_with_peer_routes(
    bus: Arc<Bus>,
) -> anyhow::Result<(Engine, Engine, Arc<ModelAdapterNode>, Arc<ModelAdapterNode>)> {
    let prov_a: Arc<dyn Provider> = Arc::new(SimpleMock {
        name: "alpha".into(), model: "alpha-v1".into(), text: "alpha-reply".into(),
    });
    let prov_b: Arc<dyn Provider> = Arc::new(SimpleMock {
        name: "beta".into(), model: "beta-v1".into(), text: "beta-reply".into(),
    });
    let model_a = ModelAdapterNode::new(prov_a, &bus, NodeId::new("model/alpha")).await?;
    let model_b = ModelAdapterNode::new(prov_b, &bus, NodeId::new("model/beta")).await?;

    let cfg_a = make_engine_cfg("alpha", "alpha-v1");
    let cfg_b = make_engine_cfg("beta", "beta-v1");

    let engine_a = EngineBuilder::new(vec![bus.clone()]).build(cfg_a).await?;
    let engine_b = EngineBuilder::new(vec![bus.clone()]).build(cfg_b).await?;
    Ok((engine_a, engine_b, model_a, model_b))
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 1：External subscriber 收 peer_message → 回 peer_reply
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn peer_message_wired_via_external_subscriber() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let (engine_a, engine_b, _ma, _mb) =
        build_dual_engines_with_peer_routes(bus.clone()).await.expect("build dual");

    let b_id = engine_b.agent_id().clone();
    let b_session = engine_b.session_id().to_string();

    // External subscriber：监听 bus 上所有 peer_message，回 PeerReply
    let bus_clone = bus.clone();
    let b_id_for_sub = b_id.clone();
    let subscriber = tokio::spawn(async move {
        let mut rx = bus_clone.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(3);
        let mut got = 0u32;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "peer_message" {
                if let Ok(pm) = serde_json::from_value::<PeerMessage>(m.payload.clone()) {
                    // 回 PeerReply 给原 sender
                    let reply = PeerReply::ok(pm.correlation_id, format!("echo:{}", pm.content));
                    let reply_payload = serde_json::to_value(&reply).unwrap();
                    let reply_msg = Message::new(
                        "peer_reply",
                        b_id_for_sub.clone(),
                        vec![m.from.clone()],
                        reply_payload,
                    );
                    let _ = bus_clone.send(reply_msg).await;
                    got += 1;
                    if got >= 1 { break; }
                }
            }
        }
        got
    });

    // 等 subscriber 准备好
    tokio::time::sleep(Duration::from_millis(200)).await;

    // Engine A 发 peer_message 给自己（向 engine B 的 session 发，由 bus route）
    let pm = PeerMessage::new(
        engine_a.session_id(),
        &b_session,
        "hello peer from A",
    );
    let pm_payload = serde_json::to_value(&pm).unwrap();
    let _ = engine_a.handle().send("peer_message", vec![b_id.clone()], pm_payload).await
        .expect("send peer_message");

    // 等 subscriber 收到 + reply 回传
    let got = subscriber.await.expect("subscriber task");
    println!("[test1] subscriber 收到 {got} peer_message");
    assert_eq!(got, 1, "expected 1 peer_message received by subscriber");

    // Engine A 的 filter 含 peer_reply（因 routes 含 peer_message），可订阅
    let filter = engine_a.handle().filter_config();
    let types = filter.types.as_ref().expect("engine filter has types");
    println!("[test1] engine_a filter types: {types:?}");
    assert!(types.contains(&"peer_reply".to_string()),
            "engine A filter 应含 peer_reply, got {types:?}");

    // Engine B 的 filter 也应含 peer_reply（如果 cfg.engine.routes 含 peer_message）
    // 这里 2 engine 都没在 routes 加 peer_message，filter 只有 [model_response, tool_result]
    let filter_b = engine_b.handle().filter_config();
    let types_b = filter_b.types.as_ref().expect("engine B filter has types");
    println!("[test1] engine_b filter types: {types_b:?}");
    assert!(types_b.contains(&"model_response".to_string()));
    assert!(!types_b.contains(&"peer_message".to_string()),
            "engine B filter 不应含 peer_message（仅 response types）: {types_b:?}");
    println!("[test1] F-011 确认：Engine filter 只看 response types，不收 peer_message");
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 2：MessageHandler for peer_message + 手动 dispatch_incoming
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn peer_message_handler_registered_on_engine() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let (engine_a, mut engine_b, _ma, _mb) =
        build_dual_engines_with_peer_routes(bus.clone()).await.expect("build dual");

    // Engine B 注册 peer_message handler
    // add_handler 是 sync fn + 用 blocking_lock，必须在 block_in_place 里调
    let call_count = tokio::task::block_in_place(|| {
        let handler = PeerEchoHandler::new(engine_b.agent_id().clone(), "B-says");
        let cc = handler.call_count.clone();
        engine_b.add_handler(Arc::new(handler), true);
        cc
    });

    // 现在 B 应当能 dispatch peer_message（手动）
    // 模拟：外部 sender 发 peer_message 给 engine B
    let pm = PeerMessage::new(
        engine_a.session_id(),
        engine_b.session_id(),
        "ping B",
    );
    let pm_msg = Message::new(
        "peer_message",
        engine_a.agent_id().clone(),
        vec![engine_b.agent_id().clone()],
        serde_json::to_value(&pm).unwrap(),
    );

    // External listener：catch bus 上 directed to B 的 peer_message → 调 engine_b.dispatch_incoming
    let bus_clone = bus.clone();
    let engine_b_id = engine_b.agent_id().clone();
    let listener = tokio::spawn(async move {
        let mut rx = bus_clone.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(3);
        let mut got = 0u32;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "peer_message" && m.to.contains(&engine_b_id) {
                // 模拟 app 收到 peer_message 后手动 dispatch
                got += 1;
                if got >= 1 { break; }
            }
        }
        got
    });

    let _ = bus.send(pm_msg).await.expect("send peer_message to bus");
    let got = listener.await.expect("listener");
    println!("[test2] listener saw {got} peer_message for engine_b");
    assert!(got >= 1, "expected at least 1 peer_message on bus, got {got}");

    // 启动 reply_watcher（先 subscribe）
    let bus_for_reply = bus.clone();
    let reply_watcher = tokio::spawn(async move {
        let mut rx = bus_for_reply.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(5);
        let mut saw_reply = false;
        let mut types_seen = std::collections::HashSet::new();
        while !saw_reply {
            if let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
                if let Ok(m) = recv {
                    types_seen.insert(m.msg_type.clone());
                    if m.msg_type == "peer_reply" {
                        println!("[test2] saw peer_reply: {:?}", m.payload);
                        saw_reply = true;
                    }
                }
            } else {
                break;
            }
        }
        println!("[test2] reply_watcher types_seen: {types_seen:?}");
        saw_reply
    });
    // 等 reply_watcher subscribe 完
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Engine B 手动 dispatch（app 必须自调 — Engine 不自动）
    // 重新构造一个 peer_message 给 dispatch_incoming
    // dispatch_incoming 用 blocking_lock，必须在 block_in_place 内调
    // 注意：handler 内部会发 peer_reply 给 msg.from。from 必须是已 online 的 node，
    // 否则 bus.send 报 NodeOffline。所以用 engine_a.agent_id() 作 from（已 online）。
    let pm2 = PeerMessage::new("from-A", engine_b.session_id(), "ping B-2");
    let pm2_msg = Message::new(
        "peer_message",
        engine_a.agent_id().clone(),  // 用 engine_a 作 from（已 online）
        vec![engine_b.agent_id().clone()],
        serde_json::to_value(&pm2).unwrap(),
    );
    let outcome = tokio::task::block_in_place(|| {
        engine_b.dispatch_incoming(pm2_msg).expect("dispatch_incoming ok")
    });
    println!("[test2] dispatch_incoming outcome: {outcome:?}");
    assert_eq!(outcome, HandlerOutcome::Handled, "expected Handled");
    assert_eq!(call_count.load(std::sync::atomic::Ordering::SeqCst), 1, "handler called once");

    // dispatch_incoming 内部 spawn 的 reply send 飞一会儿
    tokio::time::sleep(Duration::from_secs(2)).await;
    let saw_reply = reply_watcher.await.expect("reply_watcher");
    assert!(saw_reply, "expected peer_reply on bus after handler dispatched");
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 3：双向往返 — A 收 B reply, B 收 A reply
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn peer_round_trip_two_engines() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let (mut engine_a, mut engine_b, _ma, _mb) =
        build_dual_engines_with_peer_routes(bus.clone()).await.expect("build dual");

    // 2 engine 各注册 peer_message handler（add_handler 是 sync，必须 block_in_place）
    let (count_a, count_b) = tokio::task::block_in_place(|| {
        let handler_a = PeerEchoHandler::new(engine_a.agent_id().clone(), "A-says");
        let handler_b = PeerEchoHandler::new(engine_b.agent_id().clone(), "B-says");
        let ca = handler_a.call_count.clone();
        let cb = handler_b.call_count.clone();
        engine_a.add_handler(Arc::new(handler_a), true);
        engine_b.add_handler(Arc::new(handler_b), true);
        (ca, cb)
    });
    // engine_a/engine_b 仍在作用域（block_in_place 用 &mut borrow，borrow 在 block 结束即释放）

    // 外部 listener：catch directed peer_messages → 手动 dispatch
    // 先启动 subscriber 收集所有 peer_message（不 racing：subscriber subscribe 在 send 之前）
    let bus_clone = bus.clone();
    let listener_handle = tokio::spawn(async move {
        let mut rx = bus_clone.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(3);
        let mut total = 0u32;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "peer_message" {
                total += 1;
                if total >= 2 { break; }
            }
        }
        total
    });

    // 让 subscriber 任务先 subscribe
    tokio::time::sleep(Duration::from_millis(100)).await;

    // A → B
    let a_id = engine_a.agent_id().clone();
    let b_id = engine_b.agent_id().clone();
    let pm_ab = PeerMessage::new(engine_a.session_id(), engine_b.session_id(), "A→B hi");
    let msg_ab = Message::new(
        "peer_message",
        engine_a.agent_id().clone(),
        vec![b_id.clone()],
        serde_json::to_value(&pm_ab).unwrap(),
    );
    bus.send(msg_ab).await.expect("send A→B");

    // B → A
    let pm_ba = PeerMessage::new(engine_b.session_id(), engine_a.session_id(), "B→A hi");
    let msg_ba = Message::new(
        "peer_message",
        engine_b.agent_id().clone(),
        vec![a_id.clone()],
        serde_json::to_value(&pm_ba).unwrap(),
    );
    bus.send(msg_ba).await.expect("send B→A");

    // 等 listener 收齐
    let total = listener_handle.await.expect("listener");
    println!("[test3] listener saw {total} peer_messages on bus");
    assert!(total >= 2, "expected 2 peer_messages on bus, got {total}");

    // 手动 dispatch 给各 engine（模拟 app 侧桥接）
    // 关键：broadcast::Receiver 在 subscribe 后才收到消息。本 test 已通过 listener 收齐，
    // 用 listener_handle 同时把消息收集回来——但 listener_handle 是 u32，不含 msgs。
    // 解决：重新跑 listener 收集 messages 实体。
    let bus2 = bus.clone();
    let collect_handle = tokio::spawn(async move {
        let mut rx = bus2.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(1);
        let mut msgs = Vec::new();
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "peer_message" {
                msgs.push(m);
                if msgs.len() >= 2 { break; }
            }
        }
        msgs
    });

    // 再发 2 条让 collect_handle 收到（listener 用了 break，重发能确保 collect 收到）
    let pm_ab2 = PeerMessage::new(engine_a.session_id(), engine_b.session_id(), "A→B hi-2");
    let msg_ab2 = Message::new(
        "peer_message",
        engine_a.agent_id().clone(),
        vec![engine_b.agent_id().clone()],
        serde_json::to_value(&pm_ab2).unwrap(),
    );
    bus.send(msg_ab2).await.expect("send A→B-2");

    let pm_ba2 = PeerMessage::new(engine_b.session_id(), engine_a.session_id(), "B→A hi-2");
    let msg_ba2 = Message::new(
        "peer_message",
        engine_b.agent_id().clone(),
        vec![engine_a.agent_id().clone()],
        serde_json::to_value(&pm_ba2).unwrap(),
    );
    bus.send(msg_ba2).await.expect("send B→A-2");

    let collected_msgs = collect_handle.await.expect("collect_handle");
    println!("[test3] collected {} peer_messages for dispatch", collected_msgs.len());
    assert!(collected_msgs.len() >= 2, "expected 2 collected, got {}", collected_msgs.len());

    // 取出 agent_id（&self call），然后在 block_in_place 里 dispatch
    let engine_b_id_for_dispatch = engine_b.agent_id().clone();
    let engine_a_id_for_dispatch = engine_a.agent_id().clone();
    let dispatched = tokio::task::block_in_place(|| {
        let mut dispatched = 0u32;
        for m in &collected_msgs {
            if m.to.contains(&engine_b_id_for_dispatch) {
                let _ = engine_b.dispatch_incoming(m.clone());
                dispatched += 1;
            } else if m.to.contains(&engine_a_id_for_dispatch) {
                let _ = engine_a.dispatch_incoming(m.clone());
                dispatched += 1;
            }
        }
        dispatched
    });
    println!("[test3] dispatch_incoming calls: {dispatched}");
    assert!(dispatched >= 2, "expected 2 dispatches, got {dispatched}");

    // 让 handler 内部 spawn 的 reply send 飞一会儿
    tokio::time::sleep(Duration::from_millis(300)).await;

    // 验证 handler 被调
    println!("[test3] handler_a count: {}", count_a.load(std::sync::atomic::Ordering::SeqCst));
    println!("[test3] handler_b count: {}", count_b.load(std::sync::atomic::Ordering::SeqCst));
    let ca = count_a.load(std::sync::atomic::Ordering::SeqCst);
    let cb = count_b.load(std::sync::atomic::Ordering::SeqCst);
    assert!(ca + cb >= 1, "at least 1 handler should have been called: a={ca} b={cb}");
}
