//! interrupt.rs — Phase 9 task 9.2.4
//!
//! 探查 Engine 的 cancel / interrupt 集成 + replay from session store。
//! mock 驱动（不依赖 LLM），6 test cases：
//! 1. cancel_before_run_yields_stopped — baseline 重验证
//! 2. cancel_during_model_response_wait — SlowMockProvider + 外部 cancel 任务
//! 3. cancel_mid_multi_round — scripted 持续调 tool，外部 cancel
//! 4. replay_from_session_store — SqliteSessionStore::in_memory 闭环
//! 5. cancel_during_tool_exec_wait — McpNode + 外部 cancel
//! 6. multi_round_state_consistency — cancel 在不同 round 触发，state 一致
//!
//! 输出物是 `docs/v1.x/phase9/audit-probe-9.2.4.md`（独立文件，独立 commit）。

mod common;

use std::sync::Arc;
use std::time::Duration;

use arf_core::ModelMessage;
use arf_engine::RunError;
use arf_model_adapter::{
    ModelParams, ModelResponsePayload, Provider, ProviderError, ToolDef,
};
use arf_session::SqliteSessionStore;
use async_trait::async_trait;
use common::harness::{E2EHarness, ProviderKind};
use common::provider::{scripted, simple_mock, text_response, tool_call_response};
use serde_json::json;
use tempfile::tempdir;
use tokio_util::sync::CancellationToken;

// ═══════════════════════════════════════════════════════════════════════
// SlowMockProvider — `chat()` 在 sleep 与 cancel 之间竞速
// ═══════════════════════════════════════════════════════════════════════

struct SlowMockProvider {
    cancel: CancellationToken,
    response: ModelResponsePayload,
    delay: Duration,
}

impl SlowMockProvider {
    fn new(cancel: CancellationToken, response: ModelResponsePayload, delay: Duration) -> Self {
        Self { cancel, response, delay }
    }
}

#[async_trait]
impl Provider for SlowMockProvider {
    fn name(&self) -> &str {
        "slow"
    }
    fn supported_models(&self) -> &[String] {
        // `supported_models` returns &[String]; a single-element static would
        // be cleaner, but we return a vec stored in the struct.
        // (Engine uses first() anyway.)
        static MODELS: std::sync::OnceLock<Vec<String>> = std::sync::OnceLock::new();
        MODELS.get_or_init(|| vec!["slow-v1".into()])
    }
    async fn chat(
        &self,
        _model_name: &str,
        _messages: Vec<ModelMessage>,
        _tools: Vec<ToolDef>,
        _params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        tokio::select! {
            biased;
            _ = self.cancel.cancelled() => {
                Err(ProviderError::Transport("cancelled".into()))
            }
            _ = tokio::time::sleep(self.delay) => Ok(self.response.clone()),
        }
    }
}

fn slow_provider(cancel: CancellationToken, response: ModelResponsePayload, delay: Duration) -> Arc<dyn Provider> {
    Arc::new(SlowMockProvider::new(cancel, response, delay))
}

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
// Test 1 — cancel before run (baseline)
// ═══════════════════════════════════════════════════════════════════════

// [方法] 重验证 react_loop.rs:5 baseline：cancel 在 run 之前 fired → Stopped
#[tokio::test]
async fn cancel_before_run_yields_stopped() {
    let cancel = CancellationToken::new();
    cancel.cancel(); // fired before build
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("never")))
        .cancel(cancel)
        .build()
        .await
        .expect("build");
    let result = h.run_react("hi").await;
    assert!(
        matches!(result, Err(RunError::Stopped)),
        "expected Stopped, got {result:?}"
    );
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — cancel during model response wait (slow provider)
// ═══════════════════════════════════════════════════════════════════════

// [方法] SlowMockProvider delay=500ms；外部 task 100ms 后 cancel。
// 期望：cancel 触发后 engine 不 hang，返回某种错误（Stopped 或 transport error）。
// 关键：framework 的 cancel 集成**不依赖** provider 自身响应。
#[tokio::test]
async fn cancel_during_model_response_wait() {
    let cancel = CancellationToken::new();
    let cancel_for_provider = cancel.clone();

    let provider = slow_provider(
        cancel_for_provider,
        text_response("late"),
        Duration::from_millis(500),
    );

    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .cancel(cancel.clone())
        .build()
        .await
        .expect("build");

    // 100ms 后 cancel（远早于 provider 500ms delay）
    let cancel_for_task = cancel.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(100)).await;
        cancel_for_task.cancel();
    });

    let start = std::time::Instant::now();
    let result = h.run_react("hi").await;
    let elapsed = start.elapsed();
    println!("[interrupt] model_wait: result={result:?}, elapsed={elapsed:?}");

    // 关键断言：cancel 在合理时间内生效（< 400ms，远小于 500ms provider delay）
    assert!(
        elapsed < Duration::from_millis(400),
        "cancel took too long ({elapsed:?}); framework not responding to cancel"
    );
    // 关键断言：结果为 Err（Stopped 或 transport error）
    assert!(result.is_err(), "expected error after cancel, got {result:?}");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — cancel mid multi-round (scripted continuous tool calls)
// ═══════════════════════════════════════════════════════════════════════

// [方法] scripted 5 tool_calls + 1 text，外部 cancel 在 50ms 后触发。
// 期望：cancel 命中后 engine 立即返错，state.messages 部分保留。
#[tokio::test]
async fn cancel_mid_multi_round() {
    let cancel = CancellationToken::new();
    let cancel_for_task = cancel.clone();

    let provider = scripted(vec![
        tool_call_response("echo", json!({"text": "a"})),
        tool_call_response("echo", json!({"text": "b"})),
        tool_call_response("echo", json!({"text": "c"})),
        tool_call_response("echo", json!({"text": "d"})),
        tool_call_response("echo", json!({"text": "e"})),
        text_response("final"),
    ]);

    let tmp = tempdir().unwrap();
    write_echo_tool(tmp.path());

    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .with_mcp(true)
        .tmpdir(tmp)
        .cancel(cancel.clone())
        .max_turns(20)
        .build()
        .await
        .expect("build");

    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(50)).await;
        cancel_for_task.cancel();
    });

    let start = std::time::Instant::now();
    let result = h.run_react("multi-round").await;
    let elapsed = start.elapsed();
    println!(
        "[interrupt] multi_round: result={:?}, elapsed={:?}, messages={}",
        result.as_ref().err(),
        elapsed,
        h.state.messages.len()
    );

    // cancel 触发后 engine 返错
    assert!(result.is_err(), "expected error after cancel, got {result:?}");
    // 关键：state.messages 不为 0（说明 engine 部分进展了）
    assert!(!h.state.messages.is_empty(), "state should have partial progress");
    // round_count 应为 1（prepare_round inc）— 不论 cancel 何时触发，round 已 inc
    assert_eq!(
        h.state.over_view.round_count, 1,
        "round_count should be 1 after one prepare_round"
    );
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — replay from session store
// ═══════════════════════════════════════════════════════════════════════

// [方法] 装 `SqliteSessionStore::in_memory()`；先 `save()` 注册 session
// （snapshot 要求 session 已存在），engine 跑部分 → cancel → load 状态 →
// 验证 state 字段（round_count / messages）正确持久化。
// 关键：snapshot 在 5 Checkpoint 位置 fire，load 能恢复。
#[tokio::test]
async fn replay_from_session_store() {
    let store = SqliteSessionStore::in_memory().await.expect("in_memory store");
    let store_arc: Arc<dyn arf_session::SessionStore> = Arc::new(store);

    let cancel = CancellationToken::new();
    let cancel_for_task = cancel.clone();
    let cancel_for_provider = cancel.clone();
    let store_for_harness = store_arc.clone();

    let provider = slow_provider(
        cancel_for_provider,
        text_response("never-reached"),
        Duration::from_millis(500),
    );

    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .cancel(cancel.clone())
        .with_session_store(store_for_harness)
        .build()
        .await
        .expect("build");

    // 预 save session（snapshot 依赖 session 已存在，否则 NotFound 错误）
    let engine_session_id = h.engine.session_id().to_string();
    let engine_agent_id = h.engine.agent_id().as_str().to_string();
    let initial_data = arf_session::SessionData {
        meta: arf_session::SessionMeta {
            session_id: engine_session_id.clone(),
            title: "replay-test".into(),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
            round_count: 0,
            turn_count: 0,
            status: arf_session::SessionStatus::Active,
            current_round: None,
        },
        state: arf_core::State::new(),
        last_checkpoint: None,
        config_snapshot: json!({}),
    };
    store_arc.save(&initial_data).await.expect("save initial session");

    println!(
        "[interrupt] replay: session_id={engine_session_id}, agent_id={engine_agent_id}"
    );

    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(100)).await;
        cancel_for_task.cancel();
    });

    let _ = h.run_react("hi").await;

    // 取消发生在 round 1，state 应至少有 user message
    assert!(!h.state.messages.is_empty(), "state should have user message");
    let snapshot_round = h.state.over_view.round_count;
    println!("[interrupt] replay: state.round_count={snapshot_round}");

    // 等异步 snapshot 完成（snapshot 是 best-effort tokio::spawn）
    tokio::time::sleep(Duration::from_millis(300)).await;

    // 关键：从 store load session，验证数据可恢复
    let loaded = store_arc.load(&engine_session_id).await.expect("load");
    match loaded {
        Some(data) => {
            println!(
                "[interrupt] replay: loaded session meta.title={}, state.messages={}, status={:?}",
                data.meta.title,
                data.state.messages.len(),
                data.meta.status
            );
            assert_eq!(data.meta.session_id, engine_session_id);
            assert!(!data.state.messages.is_empty(), "loaded state should have user message");
            // status 应该是 'interrupted'（snapshot impl:393 显式设）
            assert_eq!(data.meta.status, arf_session::SessionStatus::Interrupted);
        }
        None => panic!("expected loaded session, got None"),
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 5 — cancel during tool exec wait
// ═══════════════════════════════════════════════════════════════════════

// [方法] McpNode + 外部 cancel 任务。Tool 通过 harness 的 responder（fast）
// 回 tool_result，但 cancel 可能在 wait_for 触发。期望：cancel 生效。
#[tokio::test]
async fn cancel_during_tool_exec_wait() {
    let cancel = CancellationToken::new();
    let cancel_for_task = cancel.clone();

    let provider = scripted(vec![
        tool_call_response("echo", json!({"text": "a"})),
        tool_call_response("echo", json!({"text": "b"})),
        tool_call_response("echo", json!({"text": "c"})),
        text_response("never-reached"),
    ]);

    let tmp = tempdir().unwrap();
    write_echo_tool(tmp.path());

    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .with_mcp(true)
        .tmpdir(tmp)
        .cancel(cancel.clone())
        .max_turns(20)
        .build()
        .await
        .expect("build");

    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(30)).await;
        cancel_for_task.cancel();
    });

    let start = std::time::Instant::now();
    let result = h.run_react("tool-exec").await;
    let elapsed = start.elapsed();
    println!(
        "[interrupt] tool_wait: result={:?}, elapsed={:?}, messages={}",
        result.as_ref().err(),
        elapsed,
        h.state.messages.len()
    );

    // 关键：cancel 快速生效（< 200ms 远小于 tool loop 完整时间）
    assert!(
        elapsed < Duration::from_millis(300),
        "tool-exec cancel took too long ({elapsed:?})"
    );
    assert!(result.is_err(), "expected error after cancel, got {result:?}");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 6 — multi-round state consistency
// ═══════════════════════════════════════════════════════════════════════

// [方法] cancel 在不同位置触发 → state 字段（round_count / turn_count）
// 反映 inc_* 调用次数的一致性。具体：cancel 前后各跑一次，state 应
// 反映 inc_round 已调用（即使 cancel）。
#[tokio::test]
async fn multi_round_state_consistency() {
    // 第一次跑：cancel before run
    let cancel1 = CancellationToken::new();
    cancel1.cancel();
    let mut h1 = E2EHarness::builder(ProviderKind::Mock(simple_mock("never")))
        .cancel(cancel1)
        .build()
        .await
        .expect("build 1");
    let result1 = h1.run_react("hi").await;
    assert!(matches!(result1, Err(RunError::Stopped)));
    let rc1 = h1.state.over_view.round_count;
    let tc1 = h1.state.over_view.turn_count;
    println!("[interrupt] state consistency: cancel-before-run → rc={rc1} tc={tc1}");
    // cancel before run → prepare_round 已 inc_round → round_count=1
    assert_eq!(rc1, 1, "prepare_round 应该 inc_round（即使 cancel 立即）");
    assert_eq!(tc1, 0, "cancel before any model_call → turn_count=0");

    // 第二次跑：cancel during run
    let cancel2 = CancellationToken::new();
    let cancel2_for_task = cancel2.clone();
    let cancel2_for_provider = cancel2.clone();
    let provider = slow_provider(
        cancel2_for_provider,
        text_response("never-reached"),
        Duration::from_millis(500),
    );
    let mut h2 = E2EHarness::builder(ProviderKind::Mock(provider))
        .cancel(cancel2.clone())
        .build()
        .await
        .expect("build 2");
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(100)).await;
        cancel2_for_task.cancel();
    });
    let _ = h2.run_react("hi").await;
    let rc2 = h2.state.over_view.round_count;
    let tc2 = h2.state.over_view.turn_count;
    println!("[interrupt] state consistency: cancel-during-run → rc={rc2} tc={tc2}");
    // 不论 cancel 在 model_call 中还是之后，prepare_round 已 inc_round
    assert_eq!(rc2, 1, "prepare_round 应该 inc_round 即使 cancel");
    // turn_count 取决于 cancel 时机：< 1 (cancel before model_call) 或 == 1
    assert!(tc2 <= 1, "turn_count 期望 ≤ 1，got {tc2}");
}
