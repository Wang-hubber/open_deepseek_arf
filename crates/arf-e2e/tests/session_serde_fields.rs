//! session_serde_fields.rs — Phase 9 task 9.10.2
//!
//! 探查 SessionMeta / SessionData 序列化字段在 DB 中的端到端分布。
//! 直接对 SqliteSessionStore API 做 save+load 字段断言。
//!
//! 4 test cases:
//! 1. session_meta_all_8_fields_persisted — SessionMeta 8 字段全 round-trip
//! 2. session_data_4_fields_distributed_correctly — SessionData 4 字段 DB 位置正确
//! 3. session_status_3_variants_persist — Active/Completed/Interrupted round-trip
//! 4. current_round_some_vs_none_persist — Option<usize> 两种状态保留
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.10.2.md`（独立文件，独立 commit）。

mod common;

use arf_session::{
    CheckpointSnapshot, SessionData, SessionMeta, SessionStatus, SessionStore, SqliteSessionStore,
};
use arf_core::{Checkpoint, ModelMessage, State};
use chrono::{TimeZone, Utc};

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — SessionMeta 8 字段全 round-trip
// ═══════════════════════════════════════════════════════════════════════

// [方法] SessionMeta 8 字段：session_id, title, created_at, updated_at,
// round_count, turn_count, status, current_round。预 save 全填 → load →
// 逐字段断言值/类型。
#[tokio::test]
async fn session_meta_all_8_fields_persisted() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let fixed_created = Utc.with_ymd_and_hms(2026, 1, 1, 12, 0, 0).unwrap();
    let fixed_updated = Utc.with_ymd_and_hms(2026, 7, 3, 8, 0, 0).unwrap();

    let data = SessionData {
        meta: SessionMeta {
            session_id: "field-test-1".into(),
            title: "Field Test 1".into(),
            created_at: fixed_created,
            updated_at: fixed_updated,
            round_count: 42,
            turn_count: 100,
            status: SessionStatus::Completed,
            current_round: Some(5),
        },
        state: State::new(),
        last_checkpoint: None,
        config_snapshot: serde_json::json!({}),
    };
    store.save(&data).await.expect("save");

    // list 验证 list() 走 SELECT 8 列（session/lib.rs:252）
    let list = store.list().await.expect("list");
    assert_eq!(list.len(), 1);
    let meta = &list[0];
    assert_eq!(meta.session_id, "field-test-1");
    assert_eq!(meta.title, "Field Test 1");
    assert_eq!(meta.created_at, fixed_created);
    assert_eq!(meta.updated_at, fixed_updated);
    assert_eq!(meta.round_count, 42);
    assert_eq!(meta.turn_count, 100);
    assert_eq!(meta.status, SessionStatus::Completed);
    assert_eq!(meta.current_round, Some(5));
    println!("[serde] list() 8 fields all match: ✓");

    // load 验证 (state_json + config_json 不影响 meta)
    let loaded = store.load("field-test-1").await.expect("load").expect("exists");
    assert_eq!(loaded.meta.session_id, "field-test-1");
    assert_eq!(loaded.meta.title, "Field Test 1");
    assert_eq!(loaded.meta.created_at, fixed_created);
    assert_eq!(loaded.meta.updated_at, fixed_updated);
    assert_eq!(loaded.meta.round_count, 42);
    assert_eq!(loaded.meta.turn_count, 100);
    assert_eq!(loaded.meta.status, SessionStatus::Completed);
    assert_eq!(loaded.meta.current_round, Some(5));
    println!("[serde] load() 8 fields all match: ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — SessionData 4 字段 DB 位置正确
// ═══════════════════════════════════════════════════════════════════════

// [方法] SessionData 4 字段在 DB 中的分布：
//   meta           → sessions 表 8 列
//   state          → sessions.state_json (JSON-serialized 整个 State 含 messages+over_view+wait_events)
//   last_checkpoint → checkpoints.payload_json (按 captured_at DESC LIMIT 1)
//   config_snapshot → sessions.config_json
// 预 save 4 字段各填不同内容 → load → 各字段内容正确读出。
#[tokio::test]
async fn session_data_4_fields_distributed_correctly() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    // state 含 2 messages (user + assistant)
    let mut state = State::new();
    state.push_message(ModelMessage::new("user", "hello"));
    state.push_message(ModelMessage::new("assistant", "hi back"));
    state.over_view.round_count = 1;
    state.over_view.turn_count = 1;
    state.over_view.context_tokens = 100;

    // last_checkpoint 填 CheckpointSnapshot
    let cp = CheckpointSnapshot {
        checkpoint: Checkpoint::AfterModelCall,
        turn_index: 3,
        pending_messages: vec![ModelMessage::new("tool", "intermediate")],
        wait_events: vec![],
        captured_at: Utc.with_ymd_and_hms(2026, 7, 3, 8, 0, 0).unwrap(),
        tasks_json: serde_json::json!({"task": "sub-1"}),
    };

    // config_snapshot 填复杂 JSON
    let config = serde_json::json!({
        "model_provider": "openai",
        "model_name": "gpt-4o",
        "max_turns": 10,
        "nested": {"k": "v", "n": 42}
    });

    let data = SessionData {
        meta: SessionMeta::new("distrib-test", "Distribution Test"),
        state: state.clone(),
        last_checkpoint: Some(cp.clone()),
        config_snapshot: config.clone(),
    };
    // NOTE: save() 不写 checkpoints 表（仅 snapshot() 写）；要保留 last_checkpoint
    // 须在 save() 后再 snapshot() 一次。F-011：save() 应也持久化 last_checkpoint。
    store.save(&data).await.expect("save");
    store.snapshot("distrib-test", &state, &cp).await.expect("snapshot");

    let loaded = store.load("distrib-test").await.expect("load").expect("exists");

    // meta
    assert_eq!(loaded.meta.session_id, "distrib-test");
    assert_eq!(loaded.meta.title, "Distribution Test");

    // state (走 state_json)
    assert_eq!(loaded.state.messages.len(), 2, "state.messages");
    assert_eq!(loaded.state.messages[0].content, "hello");
    assert_eq!(loaded.state.messages[1].content, "hi back");
    assert_eq!(loaded.state.over_view.round_count, 1);
    assert_eq!(loaded.state.over_view.turn_count, 1);
    assert_eq!(loaded.state.over_view.context_tokens, 100);
    println!("[serde] state (state_json): messages=2, over_view fields OK ✓");

    // last_checkpoint (走 checkpoints.payload_json)
    let loaded_cp = loaded
        .last_checkpoint
        .expect("last_checkpoint should be loaded");
    assert_eq!(loaded_cp.checkpoint, Checkpoint::AfterModelCall);
    assert_eq!(loaded_cp.turn_index, 3);
    assert_eq!(loaded_cp.pending_messages.len(), 1);
    assert_eq!(loaded_cp.pending_messages[0].content, "intermediate");
    assert_eq!(loaded_cp.captured_at, Utc.with_ymd_and_hms(2026, 7, 3, 8, 0, 0).unwrap());
    assert_eq!(loaded_cp.tasks_json, serde_json::json!({"task": "sub-1"}));
    println!("[serde] last_checkpoint (checkpoints.payload_json): full ✓");

    // config_snapshot (走 config_json)
    assert_eq!(loaded.config_snapshot, config);
    println!("[serde] config_snapshot (config_json): full ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — SessionStatus 3 variant round-trip
// ═══════════════════════════════════════════════════════════════════════

// [方法] SessionStatus::Active / Completed / Interrupted 3 variant 各自
// save + load → 值一致。验证 status 列的 string 表示（"active"/"completed"/"interrupted"）
// 正确往返（session/lib.rs:264, 323 parse_status 路径）。
#[tokio::test]
async fn session_status_3_variants_persist() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    for (i, status) in [
        SessionStatus::Active,
        SessionStatus::Completed,
        SessionStatus::Interrupted,
    ]
    .iter()
    .enumerate()
    {
        let sid = format!("status-{i}");
        let mut data = SessionData {
            meta: SessionMeta::new(&sid, "Status Test"),
            state: State::new(),
            last_checkpoint: None,
            config_snapshot: serde_json::json!({}),
        };
        data.meta.status = *status;
        store.save(&data).await.expect("save");
    }

    // load 3 个
    for (i, expected) in [
        SessionStatus::Active,
        SessionStatus::Completed,
        SessionStatus::Interrupted,
    ]
    .iter()
    .enumerate()
    {
        let sid = format!("status-{i}");
        let loaded = store.load(&sid).await.expect("load").expect("exists");
        assert_eq!(
            loaded.meta.status, *expected,
            "status-{i}: expected {expected:?}, got {:?}",
            loaded.meta.status
        );
        println!("[serde] status-{i} round-trip: {expected:?} ✓");
    }

    // list 验证
    let list = store.list().await.expect("list");
    assert_eq!(list.len(), 3);
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — current_round Option<usize> 状态保留
// ═══════════════════════════════════════════════════════════════════════

// [方法] current_round = Some(5) 和 None 两种状态各自 save+load → 保留。
// 验证 DB 列 `current_round INTEGER`（可空）的 NULL/Some 转换（session/lib.rs:273）。
#[tokio::test]
async fn current_round_some_vs_none_persist() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    // Some(5)
    let mut data_some = SessionData {
        meta: SessionMeta::new("cr-some", "Some Test"),
        state: State::new(),
        last_checkpoint: None,
        config_snapshot: serde_json::json!({}),
    };
    data_some.meta.current_round = Some(5);
    store.save(&data_some).await.expect("save some");

    // None
    let data_none = SessionData {
        meta: SessionMeta::new("cr-none", "None Test"),
        state: State::new(),
        last_checkpoint: None,
        config_snapshot: serde_json::json!({}),
    };
    store.save(&data_none).await.expect("save none");

    // 验证
    let loaded_some = store.load("cr-some").await.expect("load").expect("exists");
    assert_eq!(loaded_some.meta.current_round, Some(5));
    println!("[serde] current_round Some(5) round-trip: ✓");

    let loaded_none = store.load("cr-none").await.expect("load").expect("exists");
    assert_eq!(loaded_none.meta.current_round, None);
    println!("[serde] current_round None round-trip: ✓");
}
