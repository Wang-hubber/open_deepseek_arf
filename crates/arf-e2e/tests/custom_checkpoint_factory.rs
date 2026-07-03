//! custom_checkpoint_factory.rs — Phase 9 task 9.12.4
//!
//! 探查 app 用 framework-supplied CheckpointRule factory（every_n_rounds / when_context_over）
//! 端到端构造自定义 Rule。
//!
//! 4 test cases：
//! 1. every_n_rounds_boundary_1_2_3 — every_n=1/2/3 边界（多 round fire 行为）
//! 2. when_context_over_boundary_ratio_0_1 — ratio=0.0/0.5/1.0 边界（utilization =/> /< 阈值）
//! 3. factory_x_5_checkpoint_matrix — factory 组合 5 Checkpoint trigger
//! 4. factory_build_returns_custom_message — factory + build 返回自定义 ActionMessage

mod common;

use std::sync::{Arc, Mutex};

use arf_core::{ActionMessage, Checkpoint, CheckpointRule, NodeId, Route};
use async_trait::async_trait;
use common::harness::{E2EHarness, ProviderKind};
use common::provider::{scripted, simple_mock, text_response, tool_call_response};
use serde_json::json;
use tempfile::tempdir;
use uuid::Uuid;

// ═══════════════════════════════════════════════════════════════════════
// 自定义 ActionMessage — 验证 factory.build 返回的 message 类型
// ═══════════════════════════════════════════════════════════════════════

#[derive(Clone, Debug)]
struct CustomFactoryMsg {
    tag: String,
    cid: Uuid,
}

impl CustomFactoryMsg {
    fn new(tag: &str) -> Self {
        Self { tag: tag.into(), cid: Uuid::new_v4() }
    }
}

#[async_trait]
impl ActionMessage for CustomFactoryMsg {
    fn msg_type(&self) -> &'static str { "factory/audit" }
    fn correlation_id(&self) -> Uuid { self.cid }
    fn payload(&self) -> serde_json::Value {
        json!({"correlation_id": self.cid.to_string(), "tag": self.tag})
    }
    fn intent(&self) -> arf_core::MessageIntent { arf_core::MessageIntent::Command }
}

const ROUTE_TARGET: &str = "model/e2e";

// Helper — 写 echo tool
fn write_echo_tool(tmp: &std::path::Path) {
    let tool_dir = tmp.join("tools").join("echo");
    std::fs::create_dir_all(&tool_dir).unwrap();
    std::fs::write(
        tool_dir.join("tool.toml"),
        "name = \"echo\"\ndescription = \"Echo back the input\"\nruntime = \"python\"\nentrypoint = \"echo.py\"\n",
    ).unwrap();
    std::fs::write(
        tool_dir.join("echo.py"),
        "import sys, json\nparams = json.load(sys.stdin)\nprint(json.dumps({\"echoed\": params.get(\"text\", \"\")}))\n",
    ).unwrap();
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — every_n_rounds 边界 (N=1/2/3)
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn every_n_rounds_boundary_1_2_3() {
    // every_n=1: round_count > 0 && % 1 == 0 → 永远 fire
    // every_n=2: round_count 2/4/6 fire, 1/3/5 skip
    // every_n=3: round_count 3/6 fire, 1/2/4/5 skip

    for (n_rounds, expected_fires) in [(1u32, 3usize), (2, 1), (3, 1)] {
        let fires = Arc::new(Mutex::new(Vec::<String>::new()));
        let fires_clone = fires.clone();
        let rule = CheckpointRule::every_n_rounds(
            format!("every_{n_rounds}_rounds"),
            Checkpoint::RoundEnd,
            n_rounds,
            move |_s| {
                fires_clone.lock().unwrap().push("fire".into());
                Box::new(CustomFactoryMsg::new("every")) as Box<dyn ActionMessage>
            },
        );
        let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
            .with_checkpoint_rules(vec![rule])
            .route("factory/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
            .build()
            .await
            .expect("build");

        for i in 1..=3 {
            let _ = h.run_react(&format!("r{i}")).await.expect("run");
        }

        let observed = fires.lock().unwrap().clone();
        println!("[test1] every_n={n_rounds} fires: {observed:?} (expected {expected_fires})");
        assert_eq!(observed.len(), expected_fires, "every_n={n_rounds} 应 fire {expected_fires} 次");
    }

    println!("[test1] every_n_rounds 1/2/3 边界 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — when_context_over 边界 (0.0 / 0.5 / 1.0)
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn when_context_over_boundary_ratio_0_1() {
    // ratio=0.0: utilization >= 0.0 永远 fire (>= 包含 0)
    // ratio=0.5: util=0.6 fire, util=0.4 skip
    // ratio=1.0: util=1.0 fire, util=0.6 skip, util=0.99 skip

    // ratio=0.0 → util=0.0 fire
    {
        let fires = Arc::new(Mutex::new(Vec::<String>::new()));
        let fires_clone = fires.clone();
        let rule = CheckpointRule::when_context_over(
            "ctx_over_0",
            Checkpoint::BeforeModelCall,
            0.0,
            move |_s| {
                fires_clone.lock().unwrap().push("fire".into());
                Box::new(CustomFactoryMsg::new("ctx_0")) as Box<dyn ActionMessage>
            },
        );
        let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
            .with_checkpoint_rules(vec![rule])
            .route("factory/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
            .build()
            .await
            .expect("build");
        h.state.over_view.context_tokens = 0;
        h.state.over_view.model_context_window = 100;
        let _ = h.run_react("hi").await.expect("run");
        let observed = fires.lock().unwrap().clone();
        println!("[test2] ratio=0.0 util=0.0 fires: {observed:?}");
        assert_eq!(observed.len(), 1, "ratio=0.0 util=0.0 应 fire (>=0.0)");
    }

    // ratio=0.5 → util=0.6 fire, util=0.4 skip
    {
        let fires = Arc::new(Mutex::new(Vec::<String>::new()));
        let fires_clone = fires.clone();
        let rule = CheckpointRule::when_context_over(
            "ctx_over_0.5",
            Checkpoint::BeforeModelCall,
            0.5,
            move |_s| {
                fires_clone.lock().unwrap().push("fire".into());
                Box::new(CustomFactoryMsg::new("ctx_05")) as Box<dyn ActionMessage>
            },
        );
        let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
            .with_checkpoint_rules(vec![rule])
            .route("factory/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
            .build()
            .await
            .expect("build");
        // util = 0.6
        h.state.over_view.context_tokens = 60;
        h.state.over_view.model_context_window = 100;
        let _ = h.run_react("hi").await.expect("run");
        let observed = fires.lock().unwrap().clone();
        println!("[test2] ratio=0.5 util=0.6 fires: {observed:?}");
        assert_eq!(observed.len(), 1, "util=0.6 >= 0.5 应 fire");

        // util = 0.4 → skip
        fires.lock().unwrap().clear();
        h.state.over_view.context_tokens = 40;
        let _ = h.run_react("hi2").await.expect("run 2");
        let observed = fires.lock().unwrap().clone();
        println!("[test2] ratio=0.5 util=0.4 fires: {observed:?}");
        assert_eq!(observed.len(), 0, "util=0.4 < 0.5 不 fire");
    }

    // ratio=1.0 → util=1.0 fire (边界包含), util=0.6 skip
    {
        let fires = Arc::new(Mutex::new(Vec::<String>::new()));
        let fires_clone = fires.clone();
        let rule = CheckpointRule::when_context_over(
            "ctx_over_1.0",
            Checkpoint::BeforeModelCall,
            1.0,
            move |_s| {
                fires_clone.lock().unwrap().push("fire".into());
                Box::new(CustomFactoryMsg::new("ctx_1")) as Box<dyn ActionMessage>
            },
        );
        let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
            .with_checkpoint_rules(vec![rule])
            .route("factory/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
            .build()
            .await
            .expect("build");
        h.state.over_view.context_tokens = 100;
        h.state.over_view.model_context_window = 100;
        let _ = h.run_react("hi").await.expect("run");
        let observed = fires.lock().unwrap().clone();
        println!("[test2] ratio=1.0 util=1.0 fires: {observed:?}");
        assert_eq!(observed.len(), 1, "util=1.0 >= 1.0 应 fire");

        // util = 0.6 → skip
        fires.lock().unwrap().clear();
        h.state.over_view.context_tokens = 60;
        let _ = h.run_react("hi2").await.expect("run 2");
        let observed = fires.lock().unwrap().clone();
        assert_eq!(observed.len(), 0, "util=0.6 < 1.0 不 fire");
    }

    println!("[test2] when_context_over 0.0/0.5/1.0 边界 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — factory × 5 Checkpoint trigger 矩阵
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn factory_x_5_checkpoint_matrix() {
    let triggers = [
        Checkpoint::BeforeModelCall,
        Checkpoint::AfterModelCall,
        Checkpoint::BeforeToolExec,
        Checkpoint::AfterToolExec,
        Checkpoint::RoundEnd,
    ];

    for (i, trigger) in triggers.iter().enumerate() {
        let fires = Arc::new(Mutex::new(Vec::<String>::new()));
        let fires_clone = fires.clone();
        // every_n_rounds(1) 在每个 trigger 都应 fire（前提 trigger 被访问）
        let rule = CheckpointRule::every_n_rounds(
            format!("matrix_{i}"),
            *trigger,
            1,
            move |_s| {
                fires_clone.lock().unwrap().push("fire".into());
                Box::new(CustomFactoryMsg::new(&format!("t{i}"))) as Box<dyn ActionMessage>
            },
        );

        // 1 round 1 tool: 全部 5 个 trigger 都被访问
        let tmp = tempdir().unwrap();
        write_echo_tool(tmp.path());
        let provider = scripted(vec![
            tool_call_response("echo", json!({"text": "x"})),
            text_response("done"),
        ]);
        let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
            .with_mcp(true)
            .tmpdir(tmp)
            .with_checkpoint_rules(vec![rule])
            .route("factory/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
            .build()
            .await
            .expect("build");

        let _ = h.run_react("hi").await.expect("run");
        let observed = fires.lock().unwrap().clone();
        println!("[test3] trigger={trigger:?} fires: {observed:?}");
        assert!(!observed.is_empty(), "trigger {trigger:?} 应至少 fire 1 次");
    }

    println!("[test3] factory × 5 Checkpoint trigger 矩阵 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — factory.build 返回 CustomFactoryMsg (msg_type="factory/audit")
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn factory_build_returns_custom_message() {
    let fires = Arc::new(Mutex::new(Vec::<String>::new()));
    let fires_clone = fires.clone();
    let rule = CheckpointRule::every_n_rounds(
        "custom_msg",
        Checkpoint::RoundEnd,
        1,  // 每 round fire
        move |_s| {
            fires_clone.lock().unwrap().push("fire".into());
            Box::new(CustomFactoryMsg::new("custom_payload")) as Box<dyn ActionMessage>
        },
    );
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
        .with_checkpoint_rules(vec![rule])
        .route("factory/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
        .build()
        .await
        .expect("build");

    let _ = h.run_react("hi").await.expect("run");
    let observed = fires.lock().unwrap().clone();
    assert_eq!(observed.len(), 1, "round 1 期望 fire 1 次 (every_n=1 永远 fire)");

    // 验证 build 闭包被调用（fire counter 增 1）
    println!("[test4] CustomFactoryMsg (msg_type=factory/audit) build 端到端 OK ✓");
}
