//! [E2E] Core ReAct path — real Bus + real Node + real Engine run full loop.
//!
//! Test angles covered:
//! - [方法] single-round text, single-round tool call, multi-round
//! - [边界] max_turns exceeded, cancel mid-run
//!
//! All tests use a real `ModelAdapterNode` + real `Bus` (not the inline
//! mock responder pattern from `crates/arf-engine/tests/integration.rs`).
//! Provider responses come from a [`ScriptedProvider`] (local mock) or live
//! MiniMax (env-var gated) — the latter is exercised in
//! `mcp_facade.rs::facade_with_own_capabilities`.
//!
//! ## Tool-call setup
//!
//! The `E2EHarnessBuilder` supports `.tmpdir(pre)` so the test can write
//! a `tools/echo/tool.toml` + `echo.py` pair into the directory BEFORE
//! `McpNode::local()` scans it. This is the only way to make the engine
//! see a real `tool_exec` recipient and complete the ReAct tool loop.

mod common;

use common::harness::{E2EHarness, ProviderKind};
use common::provider::{simple_mock, text_response, tool_call_response};
use arf_engine::RunError;
use serde_json::json;
use tokio_util::sync::CancellationToken;

/// Write a Python-based echo tool to `tmpdir/tools/echo/`. The tool reads
/// JSON from stdin and echoes back the `text` field.
fn write_echo_tool(tmp: &std::path::Path) {
    let tool_dir = tmp.join("tools").join("echo");
    std::fs::create_dir_all(&tool_dir).unwrap();
    // ToolConfig fields are at the top level (not nested in [tool]).
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

// ── Test 1: single round, text only ─────────────────────────────────────

// [方法] 单 round 纯文本：state.messages.len() == 2 (user + assistant; 2026-07-02 system prefix 现采不入 state.messages)
#[tokio::test]
async fn react_single_round_text() {
    let mut h = E2EHarness::new(ProviderKind::Mock(simple_mock("hello world")))
        .await
        .unwrap();
    let out = h.run_react("test input").await.expect("run failed");
    assert_eq!(out, "hello world");
    h.assert_state_messages(2);
    assert!(h.state.messages[1].tool_calls.is_empty());
}

// ── Test 2: single round, one tool call (real McpNode) ──────────────────

// [方法] 单 round tool call：state.messages.len() == 3 (user + assistant + tool; 2026-07-02)
#[tokio::test]
async fn react_single_round_tool_call() {
    let tmp = tempfile::tempdir().unwrap();
    write_echo_tool(tmp.path());

    // Model sequence: 1 tool_call(echo, text=hi), then 1 text response.
    // The script must terminate — otherwise scripted falls back to the
    // last entry (the tool_call) and the engine loops forever, hitting
    // max_turns instead of returning naturally.
    let provider = common::provider::scripted(vec![
        tool_call_response("echo", json!({"text": "hi"})),
        text_response("done after one tool"),
    ]);
    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .with_mcp(true)
        .tmpdir(tmp)
        .build()
        .await
        .unwrap();
    let out = h
        .run_react("test tool call")
        .await
        .expect("run should succeed");
    // messages: user + assistant(t1) + tool(t1) + assistant(text) = 4
    // (We looped through model_call twice — once with tool_calls,
    //  once with final text — so 4 messages, not 3.)
    h.assert_state_messages(4);
    h.assert_last_tool_call("echo");
    // Final assistant message is the text, not the tool_calls one.
    assert_eq!(out, "done after one tool");
}

// ── Test 3: multi-round, consecutive tool calls then text ───────────────

// [边界] 多 round：连续 2 个 tool call，第三次 model 输出文本。
// 注：单次 run() = 1 round = 1 chat() = 1 user→final。在该 round 内，model
// 返回 tool_calls，engine 依次 tool_exec，下一次 model_call 取 scripted 末位
// 响应（本测试中脚本最后一项是 text_response）。
#[tokio::test]
async fn react_multi_round_consecutive_tools() {
    let tmp = tempfile::tempdir().unwrap();
    write_echo_tool(tmp.path());

    let provider = common::provider::scripted(vec![
        tool_call_response("echo", json!({"text": "first"})),
        tool_call_response("echo", json!({"text": "second"})),
        text_response("done after tools"),
    ]);
    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .with_mcp(true)
        .tmpdir(tmp)
        .build()
        .await
        .unwrap();
    let out = h
        .run_react("multi-round test")
        .await
        .expect("run should succeed");
    assert_eq!(out, "done after tools");
    // messages: user + assistant(t1) + tool(t1) + assistant(t2) + tool(t2) + assistant(text) = 6
    h.assert_state_messages(6);
}

// ── Test 4: max_turns exceeded ──────────────────────────────────────────

// [边界] max_turns=2：连续 tool_call 让 engine 试图 tool_exec，loop 持续
// 调 model_call，直到 turn_count 达到 max_turns → MaxTurnsExceeded。
#[tokio::test]
async fn react_max_turns_exceeded() {
    let tmp = tempfile::tempdir().unwrap();
    write_echo_tool(tmp.path());

    // 一直 tool_call：engine 永远走 tool_exec 路径，turn_count 累加。
    let provider = common::provider::scripted(vec![tool_call_response(
        "echo",
        json!({"text": "x"}),
    )]);
    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .with_mcp(true)
        .tmpdir(tmp)
        .max_turns(3)
        .build()
        .await
        .unwrap();
    let result = h.run_react("trigger max turns").await;
    assert!(
        matches!(result, Err(RunError::MaxTurnsExceeded { max_turns: 3 })),
        "expected MaxTurnsExceeded(3), got {:?}",
        result
    );
}

// ── Test 5: cancel mid-run yields Stopped ───────────────────────────────

// [边界] cancel：预 cancel 的 token 让 engine 在 loop 顶部检查时立刻返 Stopped。
#[tokio::test]
async fn react_cancel_yields_stopped() {
    let cancel = CancellationToken::new();
    cancel.cancel();
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("never seen")))
        .cancel(cancel)
        .build()
        .await
        .unwrap();
    let result = h.run_react("test cancel").await;
    assert!(
        matches!(result, Err(RunError::Stopped)),
        "expected Stopped, got {:?}",
        result
    );
}
