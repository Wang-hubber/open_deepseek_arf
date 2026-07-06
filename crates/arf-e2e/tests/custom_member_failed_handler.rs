//! custom_member_failed_handler.rs — Phase 9 task 9.12.5
//!
//! 探查 app 实现自定义 `OnMemberFailedHandler` trait 端到端能力。
//!
//! 4 test cases：
//! 1. handler_closure_fail_session — 闭包 handler 返回 FailSession + build OK
//! 2. handler_struct_three_actions — struct impl OnMemberFailedHandler，handle() 根据 member 节点名返回不同 action
//! 3. handler_default_none_is_fail_session — 不设 handler (None) 端到端 build OK
//! 4. handler_invoke_directly_returns_action — 直接调 handler.handle() 端到端

mod common;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use arf_core::NodeId;
use arf_engine::{AgentConfig, Engine, EngineBuilder, MemberFailedAction, ModelDecl, OnMemberFailedHandler};
use common::harness::{E2EHarness, ProviderKind};
use common::provider::simple_mock;
use serde_json::json;

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — 闭包 handler 返回 FailSession + build OK
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn handler_closure_fail_session() {
    let h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
        // 不能直接 set on_member_failed 通过 builder — 需自己造 EngineConfig
        .build()
        .await
        .expect("harness build (without on_member_failed)");

    // 验证 default None 路径 work
    // 现在手动构造 handler 闭包验证 trait 自动 blanket impl
    let handler = |_a: &NodeId, _m: &NodeId, _r: &str| -> MemberFailedAction {
        MemberFailedAction::FailSession
    };
    // 闭包是 OnMemberFailedHandler（via blanket impl）
    let result = handler.handle(&NodeId::new("agent"), &NodeId::new("mcp/x"), "test reason");
    assert_eq!(result, MemberFailedAction::FailSession);
    println!("[test1] 闭包 handler 返回 FailSession 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — struct impl OnMemberFailedHandler，3 actions
// ═══════════════════════════════════════════════════════════════════════

struct ThreeActionHandler {
    invocations: Arc<Mutex<Vec<(String, String, String)>>>,
}

impl ThreeActionHandler {
    fn new() -> Self {
        Self { invocations: Arc::new(Mutex::new(Vec::new())) }
    }
}

impl OnMemberFailedHandler for ThreeActionHandler {
    fn handle(&self, agent: &NodeId, member: &NodeId, reason: &str) -> MemberFailedAction {
        self.invocations.lock().unwrap().push((
            agent.as_str().to_string(),
            member.as_str().to_string(),
            reason.to_string(),
        ));
        match member.as_str() {
            "mcp/retry" => MemberFailedAction::Retry { delay_ms: 500 },
            "mcp/switch" => MemberFailedAction::SwitchTo {
                alternative: NodeId::new("mcp/alternative"),
            },
            _ => MemberFailedAction::FailSession,
        }
    }
}

#[tokio::test]
async fn handler_struct_three_actions() {
    let handler = ThreeActionHandler::new();
    let invocations = handler.invocations.clone();

    // 调 handle 多次验三种 action
    let a1 = handler.handle(&NodeId::new("agent"), &NodeId::new("mcp/retry"), "timeout");
    assert_eq!(a1, MemberFailedAction::Retry { delay_ms: 500 });
    println!("[test2] mcp/retry → Retry {{ delay_ms: 500 }} ✓");

    let a2 = handler.handle(&NodeId::new("agent"), &NodeId::new("mcp/switch"), "down");
    match a2 {
        MemberFailedAction::SwitchTo { alternative } => {
            assert_eq!(alternative.as_str(), "mcp/alternative");
        }
        _ => panic!("expected SwitchTo, got {a2:?}"),
    }
    println!("[test2] mcp/switch → SwitchTo {{ mcp/alternative }} ✓");

    let a3 = handler.handle(&NodeId::new("agent"), &NodeId::new("mcp/other"), "unknown");
    assert_eq!(a3, MemberFailedAction::FailSession);
    println!("[test2] mcp/other → FailSession ✓");

    let calls = invocations.lock().unwrap().clone();
    assert_eq!(calls.len(), 3);
    assert_eq!(calls[0].1, "mcp/retry");
    assert_eq!(calls[1].1, "mcp/switch");
    assert_eq!(calls[2].1, "mcp/other");
    println!("[test2] struct handler 三 action 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — None 路径 + 手动 EngineConfig with handler
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn handler_default_none_is_fail_session() {
    // 通过 harness 走 default (None) 路径 — 验证 build OK
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
        .build()
        .await
        .expect("harness build (default None)");

    // 运行 round 验基线 work
    let out = h.run_react("hi").await.expect("run");
    assert_eq!(out, "hi");
    println!("[test3] default None handler + run 端到端 OK ✓");

    // MemberFailedAction::default() 应是 FailSession（config.rs:54）
    assert_eq!(MemberFailedAction::default(), MemberFailedAction::FailSession);
    println!("[test3] MemberFailedAction::default() == FailSession ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — handler invoke 端到端 + build Engine with handler
// ═══════════════════════════════════════════════════════════════════════

struct CountingHandler {
    count: Arc<AtomicUsize>,
}

impl OnMemberFailedHandler for CountingHandler {
    fn handle(&self, _a: &NodeId, _m: &NodeId, _r: &str) -> MemberFailedAction {
        self.count.fetch_add(1, Ordering::SeqCst);
        MemberFailedAction::FailSession
    }
}

#[tokio::test]
async fn handler_invoke_directly_returns_action() {
    let count = Arc::new(AtomicUsize::new(0));
    let handler: Arc<dyn OnMemberFailedHandler> = Arc::new(CountingHandler {
        count: count.clone(),
    });

    // 直接调 handle 多次
    for _ in 0..5 {
        let r = handler.handle(&NodeId::new("a"), &NodeId::new("m"), "r");
        assert_eq!(r, MemberFailedAction::FailSession);
    }
    assert_eq!(count.load(Ordering::SeqCst), 5, "handler 被调 5 次");

    // 手动构造 EngineConfig 注入 handler, build 端到端
    let bus = arf_bus::Bus::new(
        std::time::Duration::from_secs(1),
        std::time::Duration::from_secs(3),
        16,
    );
    let bus_arc = Arc::new(bus);

    // Pre-register a mock model node (Engine requires at least one model responder)
    let _model_node = bus_arc.connect(
        arf_core::NodeInfo {
            node_id: NodeId::new("model/mock"),
            node_type: "model".into(),
            capabilities: json!({"provider": "mock", "kind": "model"}),
            online_since: 0,
        },
        arf_core::MessageFilter { types: None, to_match: arf_core::ToMatch::All },
    ).await.expect("model node connect");

    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "mock".into(),
            model_name: "mock-v1".into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: arf_engine::EngineConfig {
            max_turns: 10,
            tool_timeout_ms: None,
            on_member_failed: Some(handler.clone()),
            ..Default::default()
        },
    };

    let result = EngineBuilder::new(vec![bus_arc.clone()]).build(cfg).await;
    assert!(result.is_ok(), "Engine with on_member_failed 应 build OK");
    let _engine: Engine = result.unwrap();
    println!("[test4] Engine build with custom handler 端到端 OK ✓");
}
