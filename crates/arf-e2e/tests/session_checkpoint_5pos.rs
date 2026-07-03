//! session_checkpoint_5pos.rs — Phase 9 task 9.10.3
//!
//! 探查 5 Checkpoint 位置 (BeforeModelCall / AfterModelCall / BeforeToolExec /
//! AfterToolExec / RoundEnd) 各调 `snapshot` 行为一致性。
//!
//! 3 test cases:
//! 1. snapshot_fires_at_3_positions_no_tool — 1 round 无 tool → 期望 3 fires
//! 2. snapshot_fires_with_tool — 1 round 1 tool → 期望 7 fires (2 turns)
//! 3. load_returns_latest_snapshot_only — load 取 captured_at DESC LIMIT 1
//!
//! **核心断言**：load() 返回 last_checkpoint = 最后一个 fire 的 Checkpoint。
//! 期望 last_checkpoint 反映最终 ReAct 终止位置的 Checkpoint。
//!
//! **如何验 "fire 了 N 次"**：观察 turn_index（每次 inc_turn 后调 snapshot，session/lib.rs:194）。
//! turn_index 反映最后一次 snapshot 时的 turn_count。多次 run 之间 turn_index 递增。
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.10.3.md`（独立文件，独立 commit）。

mod common;

use std::sync::Arc;
use std::time::Duration;

use arf_session::{SessionData, SessionMeta, SessionStatus, SessionStore, SqliteSessionStore};
use common::harness::{E2EHarness, ProviderKind};
use common::provider::{scripted, simple_mock, text_response, tool_call_response};
use serde_json::json;
use tempfile::tempdir;

/// Build a fresh SessionData with the given session_id for pre-saving.
fn make_initial_data(sid: &str) -> SessionData {
    SessionData {
        meta: SessionMeta {
            session_id: sid.into(),
            title: "5pos test".into(),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
            round_count: 0,
            turn_count: 0,
            status: SessionStatus::Active,
            current_round: None,
        },
        state: arf_core::State::new(),
        last_checkpoint: None,
        config_snapshot: serde_json::json!({}),
    }
}

/// Count rows in `checkpoints` table for a given session_id. Uses an
/// independent rusqlite::Connection opened on the same file path (the
/// SqliteSessionStore was created with `new(path)`, not `in_memory`).
fn count_checkpoints(store: &SqliteSessionStore, sid: &str) -> usize {
    use rusqlite::Connection;
    let conn = Connection::open(store.path()).expect("open shared db");
    let n: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM checkpoints WHERE session_id = ?1",
            rusqlite::params![sid],
            |r| r.get(0),
        )
        .expect("count query");
    n as usize
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — 1 round 无 tool → 3 snapshot fires
// ═══════════════════════════════════════════════════════════════════════

// [方法] 1 round 无 tool → engine.rs:226-309 主循环触发
//   turn 1: BeforeModelCall / AfterModelCall (tool_calls.is_empty() → RoundEnd)
//   = 3 fires → checkpoints 表 3 行
//   load() 返回 last_checkpoint.checkpoint = RoundEnd
#[tokio::test]
async fn snapshot_fires_at_3_positions_no_tool() {
    let tmp = tempdir().expect("tmpdir");
    let db_path = tmp.path().join("test1.db");
    let store = Arc::new(SqliteSessionStore::new(&db_path).await.unwrap());

    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi back")))
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .build()
        .await
        .expect("harness build");

    // 预 save
    let sid = h.engine.session_id().to_string();
    store.save(&make_initial_data(&sid)).await.expect("pre-save");

    let out = h.run_react("hello").await.expect("run");
    assert_eq!(out, "hi back");

    // 等异步 snapshot 全部写完
    tokio::time::sleep(Duration::from_millis(500)).await;

    // checkpoints 表行数 = 3 (BMC / AMC / RE)
    let n = count_checkpoints(&store, &sid);
    println!("[5pos] no_tool: checkpoints rows = {n} (expected 3)");
    assert_eq!(n, 3, "1 round no tool should fire 3 snapshots");

    // load 返回 last_checkpoint = RoundEnd
    let data = store.load(&sid).await.expect("load").expect("exists");
    let cp = data.last_checkpoint.expect("snapshot");
    println!("[5pos] no_tool: last_checkpoint = {:?} turn_index={}", cp.checkpoint, cp.turn_index);
    assert_eq!(cp.checkpoint, arf_core::Checkpoint::RoundEnd);
    // 1 round = 1 turn
    assert_eq!(cp.turn_index, 1, "turn_index should be 1 after 1 round");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — 1 round 1 tool → 4+ fires (engine.rs comment 285-287)
// ═══════════════════════════════════════════════════════════════════════

// [方法] 1 round 1 tool → 期望 7 fires（2 inner turn）：
//   turn 1: BMC / AMC / BTE / ATE
//   turn 2: BMC / AMC / RE  (round 1 终止于纯文本)
//   = 7 fires → checkpoints 表 7 行
//   load 返回 last_checkpoint.checkpoint = RoundEnd
#[tokio::test]
async fn snapshot_fires_with_tool() {
    let tmp = tempdir().expect("tmpdir");
    let db_path = tmp.path().join("test2.db");
    let store = Arc::new(SqliteSessionStore::new(&db_path).await.unwrap());

    // 写 echo tool 到同一 tmpdir
    write_echo_tool(tmp.path());

    let provider = scripted(vec![
        tool_call_response("echo", json!({"text": "ping"})),
        text_response("done"),
    ]);
    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .with_mcp(true)
        .tmpdir(tmp)
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .build()
        .await
        .expect("harness build");

    let sid = h.engine.session_id().to_string();
    store.save(&make_initial_data(&sid)).await.expect("pre-save");

    let out = h.run_react("multi tool").await.expect("run");
    assert_eq!(out, "done");
    h.assert_state_messages(4); // user + assistant(t1) + tool(t1) + assistant(text)

    tokio::time::sleep(Duration::from_millis(500)).await;

    let n = count_checkpoints(&store, &sid);
    println!("[5pos] with_tool: checkpoints rows = {n} (expected 7)");
    // engine.rs:285-287 注释："per-tool checkpoint 不并发触发，app-level
    // checkpoint 围绕整批触发"——1 tool batch → BTE/ATE 各 1 次。
    // turn 1 (with tool): BMC/AMC/BTE/ATE = 4 fires
    // turn 2 (no tool, text): BMC/AMC/RE = 3 fires
    // total = 7 fires
    assert_eq!(n, 7, "1 round 1 tool should fire 7 snapshots");

    let data = store.load(&sid).await.expect("load").expect("exists");
    let cp = data.last_checkpoint.expect("snapshot");
    println!("[5pos] with_tool: last_checkpoint = {:?} turn_index={}", cp.checkpoint, cp.turn_index);
    assert_eq!(cp.checkpoint, arf_core::Checkpoint::RoundEnd);
    // 2 turns: turn 1 (model_call + tool_exec) + turn 2 (model_call text)
    // 实际: turn 1 = model_call (inc_turn 1) + tool_exec (inc_turn 2) = 2 turns
    //       turn 2 = model_call (inc_turn 3) = 3 turns
    // snapshot 路径 turn_index = state.over_view.turn_count（engine.rs:194）
    assert!(cp.turn_index >= 2, "turn_index should be >= 2 after tool use, got {}", cp.turn_index);
}

/// Write a Python-based echo tool to `tmpdir/tools/echo/`.
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
// Test 3 — load 返回 latest snapshot（captured_at DESC LIMIT 1）
// ═══════════════════════════════════════════════════════════════════════

// [方法] 多次 run_react → checkpoints 表 N 行 → load 只返回 latest 1 条。
// 验证 session/lib.rs:305 "ORDER BY captured_at DESC LIMIT 1"。
#[tokio::test]
async fn load_returns_latest_snapshot_only() {
    let tmp = tempdir().expect("tmpdir");
    let db_path = tmp.path().join("test3.db");
    let store = Arc::new(SqliteSessionStore::new(&db_path).await.unwrap());

    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("ok")))
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .build()
        .await
        .expect("harness build");

    let sid = h.engine.session_id().to_string();
    store.save(&make_initial_data(&sid)).await.expect("pre-save");

    // 2 个独立 run
    let _ = h.run_react("first").await.expect("run 1");
    tokio::time::sleep(Duration::from_millis(500)).await;
    let n_after_1 = count_checkpoints(&store, &sid);
    let cp_after_1 = store
        .load(&sid)
        .await
        .expect("load 1")
        .expect("exists")
        .last_checkpoint
        .expect("cp 1");
    println!(
        "[5pos] after run 1: rows={n_after_1}, last_checkpoint turn_index={}",
        cp_after_1.turn_index
    );

    // 关键：同一个 state object，run_react 会持续累加 turn_count / round_count
    // 第 1 次 run 后 state.round_count = 1, turn_count = 1
    // 第 2 次 run 后 state.round_count = 2, turn_count = 2
    let _ = h.run_react("second").await.expect("run 2");
    tokio::time::sleep(Duration::from_millis(500)).await;
    let n_after_2 = count_checkpoints(&store, &sid);
    let cp_after_2 = store
        .load(&sid)
        .await
        .expect("load 2")
        .expect("exists")
        .last_checkpoint
        .expect("cp 2");
    println!(
        "[5pos] after run 2: rows={n_after_2}, last_checkpoint turn_index={}",
        cp_after_2.turn_index
    );

    // checkpoints 表应累加（2 次 run × 3 fires/run = 6 行）
    assert!(
        n_after_2 > n_after_1,
        "checkpoints should accumulate: run1={n_after_1}, run2={n_after_2}"
    );
    // load() 返回 latest = run 2 的 last snapshot，turn_index 应 > run 1
    assert!(
        cp_after_2.turn_index > cp_after_1.turn_index,
        "latest snapshot turn_index should advance: run1={}, run2={}",
        cp_after_1.turn_index,
        cp_after_2.turn_index
    );
    println!(
        "[5pos] load returns latest: turn_index {} → {} ✓",
        cp_after_1.turn_index, cp_after_2.turn_index
    );
}
