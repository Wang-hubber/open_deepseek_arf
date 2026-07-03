//! checkpoint_rules.rs — Phase 9 task 9.2.3
//!
//! 探查 Engine 的 Checkpoint 注入点机制（5 Checkpoint 位置 + 自定义 Rule）。
//! mock 驱动，不依赖任何 LLM。
//!
//! 6 test cases:
//! 1. ckpt_all_positions_single_round_no_tool — 5 位置注册，1 round 无 tool → 3 fires
//! 2. ckpt_all_positions_single_round_one_tool — 5 位置注册，1 round 1 tool → 7 fires
//! 3. ckpt_every_n_rounds_builtin — built-in factory，run_react 2 次后 fire 1 次
//! 4. ckpt_when_context_over_builtin — built-in factory，set state 高 utilization → fire
//! 5. ckpt_custom_rule_via_new — CheckpointRule::new 自定义 when + build
//! 6. ckpt_undeclared_msgtype_errors — error path
//!
//! 输出物是 `docs/v1.x/phase9/audit-probe-9.2.3.md`（独立文件，独立 commit）。

mod common;

use std::sync::{Arc, Mutex};

use arf_core::{ActionMessage, Checkpoint, CheckpointRule, NodeId, Route};
use arf_engine::RunError;
use async_trait::async_trait;
use common::harness::{E2EHarness, ProviderKind};
use common::provider::{scripted, simple_mock, text_response, tool_call_response};
use serde_json::json;
use tempfile::tempdir;
use uuid::Uuid;

// ═══════════════════════════════════════════════════════════════════════
// 共享类型 — Marker ActionMessage
// ═══════════════════════════════════════════════════════════════════════

/// Minimal ActionMessage for checkpoint audit. All checkpoint rules build
/// this same type (msg_type "ckpt/audit"); tag is captured in payload for
/// diagnostic only.
#[derive(Clone, Debug)]
struct MarkerMsg {
    tag: String,
    cid: Uuid,
}

impl MarkerMsg {
    fn new(tag: &str) -> Self {
        Self {
            tag: tag.into(),
            cid: Uuid::new_v4(),
        }
    }
}

#[async_trait]
impl ActionMessage for MarkerMsg {
    fn msg_type(&self) -> &'static str {
        "ckpt/audit"
    }
    fn correlation_id(&self) -> Uuid {
        self.cid
    }
    fn payload(&self) -> serde_json::Value {
        json!({"correlation_id": self.cid.to_string(), "tag": self.tag})
    }
    fn intent(&self) -> arf_core::MessageIntent {
        arf_core::MessageIntent::Command
    }
}

/// Trace the order of checkpoint fires into a shared `Vec<String>`.
/// The `build` closure pushes the tag each time the engine dispatches
/// the rule's message (i.e., each fire → 1 entry); reading the Vec
/// after `run_react` returns gives the exact firing order.
fn trace_rule(fires: Arc<Mutex<Vec<String>>>, tag: &'static str, trigger: Checkpoint) -> CheckpointRule {
    let tag_owned = tag.to_string();
    let fires_for_build = fires.clone();
    CheckpointRule::new(
        format!("ckpt/{tag}"),
        trigger,
        |_state| true, // always fire (test gates by trigger match)
        move |_state| {
            fires_for_build.lock().unwrap().push(tag.to_string());
            Box::new(MarkerMsg::new(&tag_owned))
        },
    )
}

fn register_all_5_rules(fires: Arc<Mutex<Vec<String>>>) -> Vec<CheckpointRule> {
    vec![
        trace_rule(fires.clone(), "before_model_call", Checkpoint::BeforeModelCall),
        trace_rule(fires.clone(), "after_model_call", Checkpoint::AfterModelCall),
        trace_rule(fires.clone(), "before_tool_exec", Checkpoint::BeforeToolExec),
        trace_rule(fires.clone(), "after_tool_exec", Checkpoint::AfterToolExec),
        trace_rule(fires.clone(), "round_end", Checkpoint::RoundEnd),
    ]
}

/// Route target: use the model node (always present in harness) as the
/// Strict route target. ModelAdapterNode discards messages whose msg_type
/// is not "model_call" (node.rs:72), so ckpt/audit is delivered but
/// silently dropped — no test interference, no error.
const ROUTE_TARGET: &str = "model/e2e";

/// Write a Python-based echo tool (mirror react_loop.rs).
fn write_echo_tool(tmp: &std::path::Path) {
    let tool_dir = tmp.join("tools").join("echo");
    std::fs::create_dir_all(&tool_dir).unwrap();
    std::fs::write(
        tool_dir.join("tool.toml"),
        "name = \"echo\"\ndescription = \"Echo back the input\"\nruntime = \"python\"\nentrypoint = \"echo.py\"\n",
    )
    .unwrap();
    std::fs::write(
        tool_dir.join("echo.py"),
        "import sys, json\nparams = json.load(sys.stdin)\nprint(json.dumps({\"echoed\": params.get(\"text\", \"\")}))\n",
    )
    .unwrap();
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — 5 位置注册 + 1 round 无 tool
// ═══════════════════════════════════════════════════════════════════════

// [方法] 注册 5 条 rule（每条不同 Checkpoint trigger）。1 round 无 tool 时
// 期望 fire 顺序：BeforeModelCall → AfterModelCall → RoundEnd（3 fires）；
// Before/AfterToolExec 不 fire（无 tool_call）。
#[tokio::test]
async fn ckpt_all_positions_single_round_no_tool() {
    let fires = Arc::new(Mutex::new(Vec::<String>::new()));
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hello")))
        .with_checkpoint_rules(register_all_5_rules(fires.clone()))
        .route("ckpt/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
        .build()
        .await
        .expect("harness build");

    let out = h.run_react("hi").await.expect("run");
    assert_eq!(out, "hello");
    h.assert_state_messages(2); // user + assistant

    let observed = fires.lock().unwrap().clone();
    println!("[ckpt] no_tool fires: {observed:?}");
    assert_eq!(
        observed,
        vec![
            "before_model_call".to_string(),
            "after_model_call".to_string(),
            "round_end".to_string(),
        ],
        "1 round no tool: expected [bmc, amc, re]"
    );
    // 5 个位置中有 2 个不 fire（tool_exec 位置无 tool）
    assert!(!observed.iter().any(|t| t == "before_tool_exec"));
    assert!(!observed.iter().any(|t| t == "after_tool_exec"));
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — 5 位置注册 + 1 round 1 tool
// ═══════════════════════════════════════════════════════════════════════

// [方法] 同上 + scripted 1 tool_call + 1 text response。
// 期望 fire 顺序（按 engine.rs 注释 212-219 + 285-287 "per-tool checkpoint
// 不并发触发，app-level checkpoint 围绕整批触发"）：
//  turn 1: bmc → amc → bte → ate
//  turn 2: bmc → amc → re
// = 7 fires 7 entries。
#[tokio::test]
async fn ckpt_all_positions_single_round_one_tool() {
    let fires = Arc::new(Mutex::new(Vec::<String>::new()));
    let provider = scripted(vec![
        tool_call_response("echo", json!({"text": "ping"})),
        text_response("done"),
    ]);
    let tmp = tempdir().unwrap();
    write_echo_tool(tmp.path());

    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .with_mcp(true)
        .tmpdir(tmp)
        .with_checkpoint_rules(register_all_5_rules(fires.clone()))
        .route("ckpt/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
        .build()
        .await
        .expect("harness build");

    let out = h.run_react("multi tool").await.expect("run");
    assert_eq!(out, "done");
    // messages: user + assistant(t1) + tool(t1) + assistant(text) = 4
    h.assert_state_messages(4);

    let observed = fires.lock().unwrap().clone();
    println!("[ckpt] one_tool fires: {observed:?}");
    // 全 5 位置都至少 fire 一次
    for tag in [
        "before_model_call",
        "after_model_call",
        "before_tool_exec",
        "after_tool_exec",
        "round_end",
    ] {
        assert!(
            observed.iter().any(|t| t == tag),
            "missing fire for {tag}: {observed:?}"
        );
    }
    // 数量断言：1 round = 2 个 inner turn
    let bmc_count = observed.iter().filter(|t| *t == "before_model_call").count();
    let amc_count = observed.iter().filter(|t| *t == "after_model_call").count();
    let bte_count = observed.iter().filter(|t| *t == "before_tool_exec").count();
    let ate_count = observed.iter().filter(|t| *t == "after_tool_exec").count();
    let re_count = observed.iter().filter(|t| *t == "round_end").count();
    assert_eq!(bmc_count, 2, "BeforeModelCall 期望 2 次（每 turn 顶）");
    assert_eq!(amc_count, 2, "AfterModelCall 期望 2 次（每 turn 末）");
    assert_eq!(bte_count, 1, "BeforeToolExec 期望 1 次（围绕 1 tool batch）");
    assert_eq!(ate_count, 1, "AfterToolExec 期望 1 次");
    assert_eq!(re_count, 1, "RoundEnd 期望 1 次（仅 1 round）");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — built-in every_n_rounds factory
// ═══════════════════════════════════════════════════════════════════════

// [构造] CheckpointRule::every_n_rounds(n=2)：连续 run_react 两次，
// 第 2 次 round_count=2 → fire 1 次（round 1 跳过因 round_count=1）。
#[tokio::test]
async fn ckpt_every_n_rounds_builtin() {
    let fires = Arc::new(Mutex::new(Vec::<String>::new()));
    let fires_clone = fires.clone();
    let rule = CheckpointRule::every_n_rounds(
        "every_2_rounds",
        Checkpoint::RoundEnd,
        2,
        move |_s| {
            fires_clone.lock().unwrap().push("every_2".into());
            // 用 MarkerMsg（msg_type="ckpt/audit"）以走用户注册 route
            Box::new(MarkerMsg::new("every_2"))
        },
    );
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
        .with_checkpoint_rules(vec![rule])
        .route("ckpt/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
        .build()
        .await
        .expect("build");

    // Round 1: round_count=1, 1 % 2 != 0 → no fire
    let _ = h.run_react("first").await.expect("run 1");
    let after_first = fires.lock().unwrap().clone();
    assert_eq!(after_first, Vec::<String>::new(), "round 1 should not fire");

    // Round 2: round_count=2, 2 % 2 == 0 → fire 1 次
    let _ = h.run_react("second").await.expect("run 2");
    let after_second = fires.lock().unwrap().clone();
    println!("[ckpt] every_2 fires: {after_second:?}");
    assert_eq!(after_second.len(), 1, "round 2 should fire once");
    assert_eq!(after_second[0], "every_2");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — built-in when_context_over factory
// ═══════════════════════════════════════════════════════════════════════

// [构造] CheckpointRule::when_context_over(0.5)：预设 state.context_tokens/
// model_context_window=0.6 → fire。
#[tokio::test]
async fn ckpt_when_context_over_builtin() {
    let fires = Arc::new(Mutex::new(Vec::<String>::new()));
    let fires_clone = fires.clone();
    let rule = CheckpointRule::when_context_over(
        "when_ctx_over_0.5",
        Checkpoint::BeforeModelCall,
        0.5,
        move |_s| {
            fires_clone.lock().unwrap().push("ctx_over".into());
            Box::new(MarkerMsg::new("ctx_over"))
        },
    );
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
        .with_checkpoint_rules(vec![rule])
        .route("ckpt/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
        .build()
        .await
        .expect("build");

    // 预设 context utilization = 0.6 > 0.5 → 期望 fire
    h.state.over_view.context_tokens = 60;
    h.state.over_view.model_context_window = 100;

    let _ = h.run_react("hi").await.expect("run");
    let observed = fires.lock().unwrap().clone();
    println!("[ckpt] when_ctx_over fires: {observed:?}");
    assert_eq!(observed, vec!["ctx_over".to_string()]);

    // 边界：utilization=0.4 < 0.5 → no fire
    h.state.over_view.context_tokens = 40;
    h.state.over_view.model_context_window = 100;
    fires.lock().unwrap().clear();
    let _ = h.run_react("hi2").await.expect("run 2");
    let after_low = fires.lock().unwrap().clone();
    assert!(after_low.is_empty(), "utilization=0.4 should not fire");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 5 — 自定义 CheckpointRule (CheckpointRule::new)
// ═══════════════════════════════════════════════════════════════════════

// [方法] E - Extensible：app 用 CheckpointRule::new 自定义 when 闭包
// （round_count > 3）和 build 闭包（返回自定义 MarkerMsg）。验证 trait
// 边界可用，framework 不需任何新抽象。
#[tokio::test]
async fn ckpt_custom_rule_via_new() {
    let fires = Arc::new(Mutex::new(Vec::<String>::new()));
    let fires_for_when = fires.clone();
    let fires_for_build = fires.clone();
    let rule = CheckpointRule::new(
        "custom_round_gt_3",
        Checkpoint::RoundEnd,
        move |s| {
            let fires = s.over_view.round_count > 3;
            if fires {
                fires_for_when.lock().unwrap().push("custom_when".into());
            }
            fires
        },
        move |_s| {
            fires_for_build.lock().unwrap().push("custom_build".into());
            Box::new(MarkerMsg::new("custom"))
        },
    );
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("ok")))
        .with_checkpoint_rules(vec![rule])
        .route("ckpt/audit", Route::Strict(vec![NodeId::new(ROUTE_TARGET)]))
        .build()
        .await
        .expect("build");

    // Round 1-3: round_count <= 3 → no fire
    for i in 1..=3 {
        let _ = h.run_react(&format!("r{i}")).await.expect("run");
        let observed = fires.lock().unwrap().clone();
        assert!(observed.is_empty(), "round {i} (round_count={i}) should not fire");
    }
    // Round 4: round_count=4 > 3 → fire
    let _ = h.run_react("r4").await.expect("run 4");
    let observed = fires.lock().unwrap().clone();
    println!("[ckpt] custom fires: {observed:?}");
    assert_eq!(observed.len(), 2, "when + build both push, 2 entries");
    assert_eq!(observed[0], "custom_when");
    assert_eq!(observed[1], "custom_build");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 6 — undeclared msg_type error path
// ═══════════════════════════════════════════════════════════════════════

// [边界] rule build 返回 msg_type 未在 routes 注册 → RunError::UndeclaredMsgType
// （engine/checkpoint.rs:140-144）。验证 framework 不静默失效。
#[tokio::test]
async fn ckpt_undeclared_msgtype_errors() {
    #[derive(Clone, Debug)]
    struct UndeclaredMsg;
    #[async_trait]
    impl ActionMessage for UndeclaredMsg {
        fn msg_type(&self) -> &'static str {
            "ckpt/never_registered"
        }
        fn correlation_id(&self) -> Uuid {
            Uuid::new_v4()
        }
        fn payload(&self) -> serde_json::Value {
            json!({})
        }
        fn intent(&self) -> arf_core::MessageIntent {
            arf_core::MessageIntent::Command
        }
    }

    let rule = CheckpointRule::new(
        "bad_rule",
        Checkpoint::BeforeModelCall,
        |_s| true,
        |_s| Box::new(UndeclaredMsg),
    );
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
        .with_checkpoint_rules(vec![rule])
        // 不注册 "ckpt/never_registered" route → 期望 UndeclaredMsgType
        .build()
        .await
        .expect("build");

    let result = h.run_react("hi").await;
    match result {
        Err(RunError::UndeclaredMsgType { msg_type }) => {
            assert_eq!(msg_type, "ckpt/never_registered");
            println!("[ckpt] undeclared error: msg_type={msg_type} ✓");
        }
        other => panic!("expected UndeclaredMsgType, got {other:?}"),
    }
}
