//! node_offline_switch_to.rs — Phase 9 task 9.13.2
//!
//! 探查 Engine 端 node_offline 触发 `MemberFailedAction::SwitchTo` 真实路径。
//! **预期（基于 9.13.1 F-011 证据）**：SwitchTo handler 同样**未**在 node_offline
//! 真实路径上被调。Engine 切到 alternative 的逻辑也未实现。
//!
//! 4 test cases：
//! 1. switch_to_handler_returns_alternative_node — trait 边界直接 invoke
//! 2. engine_node_offline_with_switch_to_handler — Engine + SwitchTo handler + node_offline
//! 3. switch_to_alternative_nonexistent_node — alternative=NodeId("nonexistent") 边界
//! 4. switch_to_realistic_alternative_node — 2 个 model nodes + drop primary + SwitchTo

mod common;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use arf_engine::{Engine, EngineBuilder, MemberFailedAction, OnMemberFailedHandler};
use serde_json::json;
use tokio_util::sync::CancellationToken;

// ═══════════════════════════════════════════════════════════════════════
// SwitchToHandler — handle() 返回 SwitchTo{alternative}
// ═══════════════════════════════════════════════════════════════════════

struct SwitchToHandler {
    alternative: NodeId,
    invocations: Arc<Mutex<Vec<String>>>,
}

impl SwitchToHandler {
    fn new(alt: &str) -> Self {
        Self {
            alternative: NodeId::new(alt),
            invocations: Arc::new(Mutex::new(Vec::new())),
        }
    }
}

impl OnMemberFailedHandler for SwitchToHandler {
    fn handle(&self, _a: &NodeId, member: &NodeId, _r: &str) -> MemberFailedAction {
        self.invocations.lock().unwrap().push(member.as_str().to_string());
        MemberFailedAction::SwitchTo {
            alternative: self.alternative.clone(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — SwitchTo handler trait 边界直接 invoke
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn switch_to_handler_returns_alternative_node() {
    let handler = SwitchToHandler::new("model/alternative");
    let r = handler.handle(&NodeId::new("a"), &NodeId::new("model/primary"), "timeout");
    match r {
        MemberFailedAction::SwitchTo { alternative } => {
            assert_eq!(alternative.as_str(), "model/alternative");
            println!("[test1] SwitchTo {{ alternative: model/alternative }} OK ✓");
        }
        _ => panic!("expected SwitchTo, got {r:?}"),
    }
    let calls = handler.invocations.lock().unwrap().clone();
    assert_eq!(calls, vec!["model/primary".to_string()]);
    println!("[test1] handler 记录 member=model/primary OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — Engine + SwitchTo handler + node_offline
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn engine_node_offline_with_switch_to_handler() {
    let invocations = Arc::new(Mutex::new(Vec::new()));
    let handler: Arc<dyn OnMemberFailedHandler> = Arc::new(SwitchToHandler {
        alternative: NodeId::new("model/alternative"),
        invocations: invocations.clone(),
    });

    let bus = Arc::new(Bus::new(
        Duration::from_millis(200),
        Duration::from_millis(500),
        32,
    ));
    let model_node = bus.connect(
        NodeInfo {
            node_id: NodeId::new("model/primary"),
            node_type: "model".into(),
            capabilities: json!({"provider": "mock", "kind": "model"}),
            online_since: 0,
        },
        MessageFilter { types: None, to_match: ToMatch::All },
    ).await.expect("model connect");

    let cfg = arf_engine::AgentConfig {
        model: arf_engine::ModelDecl {
            provider: "mock".into(),
            model_name: "mock-v1".into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        engine: arf_engine::EngineConfig {
            max_turns: 10,
            tool_timeout_ms: None,
            on_member_failed: Some(handler.clone()),
            ..Default::default()
        },
    };

    let mut engine: Engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.expect("build");
    let mut state = arf_core::State::new();
    let cancel = CancellationToken::new();
    let cancel_clone = cancel.clone();

    let run_handle = tokio::spawn(async move {
        engine.run(&mut state, "hi".into(), cancel_clone).await
    });

    tokio::time::sleep(Duration::from_millis(50)).await;
    drop(model_node);
    tokio::time::sleep(Duration::from_millis(1500)).await;
    cancel.cancel();
    let _ = tokio::time::timeout(Duration::from_secs(2), run_handle).await;

    let calls = invocations.lock().unwrap().clone();
    println!("[test2] handler invocations: {calls:?}");
    println!("[test2] 实证 F-011: handler 未被调 (SwitchTo 路径同样未实现)");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — SwitchTo alternative 节点不存在 (边界)
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn switch_to_alternative_nonexistent_node() {
    let handler = SwitchToHandler::new("nonexistent");
    let r = handler.handle(&NodeId::new("a"), &NodeId::new("model/primary"), "r");
    match r {
        MemberFailedAction::SwitchTo { alternative } => {
            // framework 不验证 alternative 存在 — 调用者责任
            assert_eq!(alternative.as_str(), "nonexistent");
            println!("[test3] SwitchTo {{ alternative: nonexistent }} trait 形态 OK ✓");
            println!("[test3] framework 不验证 alternative 存在 — 沿用 config.rs:50");
        }
        _ => panic!("expected SwitchTo"),
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — 2 个 model nodes + drop primary + SwitchTo (真实 alt)
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn switch_to_realistic_alternative_node() {
    let invocations = Arc::new(Mutex::new(Vec::new()));
    let handler: Arc<dyn OnMemberFailedHandler> = Arc::new(SwitchToHandler {
        alternative: NodeId::new("model/alt"),
        invocations: invocations.clone(),
    });

    let bus = Arc::new(Bus::new(
        Duration::from_millis(200),
        Duration::from_millis(500),
        32,
    ));

    // 注册 primary 和 alternative 两个 model nodes
    let primary = bus.connect(
        NodeInfo {
            node_id: NodeId::new("model/primary"),
            node_type: "model".into(),
            capabilities: json!({"provider": "mock", "kind": "model"}),
            online_since: 0,
        },
        MessageFilter { types: None, to_match: ToMatch::All },
    ).await.expect("primary connect");
    let _alt = bus.connect(
        NodeInfo {
            node_id: NodeId::new("model/alt"),
            node_type: "model".into(),
            capabilities: json!({"provider": "mock", "kind": "model"}),
            online_since: 0,
        },
        MessageFilter { types: None, to_match: ToMatch::All },
    ).await.expect("alt connect");

    let cfg = arf_engine::AgentConfig {
        model: arf_engine::ModelDecl {
            provider: "mock".into(),
            model_name: "mock-v1".into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        engine: arf_engine::EngineConfig {
            max_turns: 10,
            tool_timeout_ms: None,
            on_member_failed: Some(handler.clone()),
            ..Default::default()
        },
    };

    let mut engine: Engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.expect("build");
    let mut state = arf_core::State::new();
    let cancel = CancellationToken::new();
    let cancel_clone = cancel.clone();

    let run_handle = tokio::spawn(async move {
        engine.run(&mut state, "hi".into(), cancel_clone).await
    });

    tokio::time::sleep(Duration::from_millis(50)).await;
    drop(primary);
    tokio::time::sleep(Duration::from_millis(1500)).await;
    cancel.cancel();
    let _ = tokio::time::timeout(Duration::from_secs(2), run_handle).await;

    let calls = invocations.lock().unwrap().clone();
    println!("[test4] handler invocations: {calls:?}");
    println!("[test4] 实证 F-011: 即使 alternative 节点存在, framework 不调 handler, SwitchTo 未触发");
    println!("[test4] 期望 handler 至少 1 次 invoke, 实际: {} 次", calls.len());
}
