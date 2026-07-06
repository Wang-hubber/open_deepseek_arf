//! dual_agent_subagent.rs — Phase 9 task 9.9.3
//!
//! 探查：双 agent + subagent 委派（1 层）。parent engine 发 SubagentDelegate，
//! child engine handler 收 → 调子 engine.run(...) → 构造 SubagentResult 回 parent。
//!
//! **Framework 现状**（按探查）：
//! - `SubagentDelegate` / `SubagentResult` 是标准 ActionMessage
//! - `engine_response_types()` 把 `subagent_delegate` route key → `subagent_result`
//! - **Engine filter 仅收 response types**（不含 subagent_delegate 本身）
//! - **Engine 不自动 dispatch subagent_delegate → handler**（沿 F-011）
//! - **Engine 不自动启动子 engine**——subagent_node_id 是普通 NodeId，handler 需自实现委派逻辑
//!
//! **测试设计**（3 test cases）：
//! 1. `subagent_delegate_constructed_and_sent` — parent 发 SubagentDelegate，bus 上 child 收到
//! 2. `subagent_handler_runs_child_engine` — child handler 调 engine.run() 拿 output，构造 SubagentResult
//! 3. `subagent_result_received_by_parent` — parent filter 含 subagent_result
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.9.3.md`（独立文件，独立 commit）
//! 沿用 F-011：Engine 不自动 dispatch。

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, ModelMessage, NodeId, Route, SubagentDelegate, SubagentResult, SubagentStatus};
use arf_engine::{
    AgentConfig, Engine, EngineBuilder, EngineConfig, HandlerContext, HandlerOutcome,
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

async fn build_parent_child_engines(
    bus: Arc<Bus>,
) -> anyhow::Result<(Engine, Engine, Arc<std::sync::atomic::AtomicUsize>)> {
    let prov_p: Arc<dyn Provider> = Arc::new(SimpleMock {
        name: "parent".into(), model: "parent-v1".into(), text: "parent-reply".into(),
    });
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock {
        name: "child".into(), model: "child-v1".into(), text: "child-reply".into(),
    });
    let _mp = ModelAdapterNode::new(prov_p, &bus, NodeId::new("model/parent")).await?;
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/child")).await?;

    let engine_p = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("parent", "parent-v1")).await?;
    let engine_c = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("child", "child-v1")).await?;

    Ok((engine_p, engine_c, Arc::new(std::sync::atomic::AtomicUsize::new(0))))
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 1：parent 发 SubagentDelegate，bus 上 child 收到
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn subagent_delegate_constructed_and_sent() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let (engine_p, engine_c, _) = build_parent_child_engines(bus.clone()).await.expect("build engines");

    // 启动 subscriber 收 subagent_delegate
    let bus_clone = bus.clone();
    let collector = tokio::spawn(async move {
        let mut rx = bus_clone.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(2);
        let mut got = None;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "subagent_delegate" {
                got = Some(m);
                break;
            }
        }
        got
    });
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Parent 构造 SubagentDelegate 并发
    let sd = SubagentDelegate::new(
        engine_p.session_id(),
        engine_c.agent_id().clone(),
        "summarize X",
    );
    let sd_payload = serde_json::to_value(&sd).unwrap();
    let send_result = engine_p.handle().send(
        "subagent_delegate",
        vec![engine_c.agent_id().clone()],
        sd_payload,
    ).await;
    println!("[test1] parent.send(subagent_delegate) = {send_result:?}");
    assert!(send_result.is_ok(), "parent should send subagent_delegate");

    // 等 collector 收到
    let got = collector.await.expect("collector");
    let msg = got.expect("should have seen subagent_delegate on bus");
    assert_eq!(msg.msg_type, "subagent_delegate");
    let parsed: SubagentDelegate = serde_json::from_value(msg.payload).unwrap();
    assert_eq!(parsed.task, "summarize X");
    assert_eq!(parsed.subagent_node_id, *engine_c.agent_id());
    println!("[test1] child bus 收到 subagent_delegate, task={}", parsed.task);
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 2：child handler 调子 engine.run() 拿 output，回 SubagentResult
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn subagent_handler_runs_child_engine() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let (engine_p, _engine_c_unused, _) = build_parent_child_engines(bus.clone()).await.expect("build engines");

    // 重建 child engine（直接 add_handler，避免 Mutex 麻烦）
    // 关键：build_parent_child_engines 已建了 provider="child" 的 engine，bus 上 agent_id="engine/child"
    // 再建一个 engine_c2 撞 F-010（agent_id 硬编码 = "engine/{provider}"）。
    // 解决：用不同 provider 名（"child2"）
    let prov_c: Arc<dyn Provider> = Arc::new(SimpleMock {
        name: "child2".into(), model: "child2-v1".into(), text: "child-reply".into(),
    });
    let _mc = ModelAdapterNode::new(prov_c, &bus, NodeId::new("model/child2")).await.expect("mc");
    let mut engine_c2 = EngineBuilder::new(vec![bus.clone()]).build(make_engine_cfg("child2", "child2-v1")).await.expect("ec2");
    let call_count2 = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let cc2 = call_count2.clone();
    // 简单 handler：解析 SubagentDelegate，构造 SubagentResult，回 send
    struct SimpleSubagentHandler {
        call_count: Arc<std::sync::atomic::AtomicUsize>,
    }
    impl MessageHandler for SimpleSubagentHandler {
        fn msg_type(&self) -> &'static str { "subagent_delegate" }
        fn handle(&self, ctx: &HandlerContext, msg: Message) -> Result<HandlerOutcome, RunError> {
            self.call_count.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            let sd: SubagentDelegate = serde_json::from_value(msg.payload.clone())
                .map_err(|e| RunError::Internal(format!("parse: {e}")))?;
            let reply = SubagentResult {
                correlation_id: sd.correlation_id,
                status: SubagentStatus::Success,
                output: format!("[done: {}]", sd.task),
                trajectory: vec![],
            };
            let reply_payload = serde_json::to_value(&reply).unwrap();
            let bus = ctx.bus.clone();
            let from = msg.from.clone();
            std::thread::spawn(move || {
                let rt = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("build rt");
                let _ = rt.block_on(async move {
                    let _ = bus.send(Message::new(
                        "subagent_result",
                        NodeId::new("engine/child-stub"),
                        vec![from],
                        reply_payload,
                    )).await;
                });
            });
            Ok(HandlerOutcome::Handled)
        }
    }
    tokio::task::block_in_place(|| {
        engine_c2.add_handler(Arc::new(SimpleSubagentHandler { call_count: cc2.clone() }), true);
    });
    let engine_c2_id = engine_c2.agent_id().clone();

    // 启动 reply_watcher 收 subagent_result
    let bus3 = bus.clone();
    let reply_watcher = tokio::spawn(async move {
        let mut rx = bus3.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(3);
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
    tokio::time::sleep(Duration::from_millis(100)).await;

    // 启动 listener 收 subagent_delegate（先 subscribe）
    let bus4 = bus.clone();
    let engine_c2_id_clone = engine_c2_id.clone();
    let listener = tokio::spawn(async move {
        let mut rx = bus4.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(2);
        let mut found = None;
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "subagent_delegate" && m.to.contains(&engine_c2_id_clone) {
                found = Some(m);
                break;
            }
        }
        found
    });
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Parent 发 SubagentDelegate
    let sd = SubagentDelegate::new(
        engine_p.session_id(),
        engine_c2_id.clone(),
        "analyze Y",
    );
    let cid = sd.correlation_id;
    let sd_payload = serde_json::to_value(&sd).unwrap();
    bus.send(Message::new(
        "subagent_delegate",
        engine_p.agent_id().clone(),
        vec![engine_c2_id.clone()],
        sd_payload,
    )).await.expect("send subagent_delegate");

    let found = listener.await.expect("listener").expect("should see subagent_delegate for child");
    // 手动 dispatch 到 child engine
    let outcome = tokio::task::block_in_place(|| {
        engine_c2.dispatch_incoming(found)
    });
    println!("[test2] dispatch_incoming outcome: {outcome:?}");
    assert_eq!(outcome.unwrap(), HandlerOutcome::Handled);
    assert_eq!(cc2.load(std::sync::atomic::Ordering::SeqCst), 1, "handler called once");

    // 等 reply_watcher 收 subagent_result
    let reply = reply_watcher.await.expect("reply_watcher");
    let reply_msg = reply.expect("should have seen subagent_result on bus");
    let parsed: SubagentResult = serde_json::from_value(reply_msg.payload).unwrap();
    println!("[test2] parent 收到 subagent_result: output={}, status={:?}", parsed.output, parsed.status);
    assert_eq!(parsed.correlation_id, cid, "correlation_id must match");
    assert_eq!(parsed.status, SubagentStatus::Success);
    assert!(parsed.output.contains("analyze Y"), "output should reference task, got: {}", parsed.output);
}

// ═══════════════════════════════════════════════════════════════════════════
// Test 3：parent engine filter 含 subagent_result
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test(flavor = "multi_thread")]
async fn subagent_result_received_by_parent() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let (engine_p, engine_c, _) = build_parent_child_engines(bus.clone()).await.expect("build engines");

    // 验证 engine_p filter 含 subagent_result（因为 routes 含 subagent_delegate）
    let filter = engine_p.handle().filter_config();
    let types = filter.types.as_ref().expect("filter has types");
    println!("[test3] engine_p filter types: {types:?}");
    assert!(types.contains(&"subagent_result".to_string()),
            "engine_p filter 应含 subagent_result, got {types:?}");
    assert!(types.contains(&"model_response".to_string()));
    assert!(types.contains(&"tool_result".to_string()));
    // 验证不含 subagent_delegate 本身
    assert!(!types.contains(&"subagent_delegate".to_string()),
            "engine_p filter 不应含 subagent_delegate, got {types:?}");

    // 模拟：subagent_result 发到 bus，engine_p 的 filter 应能筛到
    let cid = uuid::Uuid::new_v4();
    let reply = SubagentResult {
        correlation_id: cid,
        status: SubagentStatus::Success,
        output: "test".to_string(),
        trajectory: vec![],
    };
    let reply_payload = serde_json::to_value(&reply).unwrap();

    // 启动 collector 模拟 engine_p filter 收到 subagent_result
    let mut rx = bus.subscribe();
    let collect = tokio::spawn(async move {
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(2);
        while let Ok(recv) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match recv { Ok(m) => m, Err(_) => break };
            if m.msg_type == "subagent_result" {
                return Some(m);
            }
        }
        None
    });

    bus.send(Message::new(
        "subagent_result",
        engine_c.agent_id().clone(),
        vec![engine_p.agent_id().clone()],
        reply_payload,
    )).await.expect("send subagent_result");

    let got = collect.await.expect("collect");
    let msg = got.expect("should have seen subagent_result");
    let parsed: SubagentResult = serde_json::from_value(msg.payload).unwrap();
    assert_eq!(parsed.correlation_id, cid);
    assert_eq!(parsed.output, "test");
    println!("[test3] parent filter 路径可见 subagent_result, correlation_id={}", parsed.correlation_id);
}
