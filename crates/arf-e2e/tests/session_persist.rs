//! session_persist.rs — Phase 9 task 9.10.1
//!
//! 探查 EngineBuilder + SqliteSessionStore 端到端 session 持久化。
//! mock 驱动，不依赖任何 LLM。
//!
//! 4 test cases:
//! 1. engine_builder_installs_session_store_and_saves — EngineBuilder.with_session_store + 1 round + load
//! 2. session_id_defaults_to_agent_id — 不设 with_session_id → load(agent_id) 找到
//! 3. session_id_override_via_builder — with_session_id("custom") → load("custom") 找到
//! 4. session_data_load_returns_all_4_fields — 4 字段 (meta/state/last_checkpoint/config_snapshot) 全可见
//!
//! **设计 quirk**（F-010 候选）：framework 的 `snapshot()` 假设 session 已存在
//! （session/lib.rs:382-399 explicit NotFound）。Engine.run() **不**自动 save 初始 session，
//! app 须自己预 save。本测试都按 interrupt.rs:270 模式预 save。
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.10.1.md`（独立文件，独立 commit）。

mod common;

use std::sync::Arc;
use std::time::Duration;

use arf_engine::RunError;
use arf_session::{SessionData, SessionMeta, SessionStatus, SessionStore, SqliteSessionStore};
use common::harness::{E2EHarness, ProviderKind};
use common::provider::simple_mock;

/// Build a fresh SessionData with the given session_id for pre-saving.
/// Mirrors interrupt.rs:255-269 pattern.
fn make_initial_data(sid: &str) -> SessionData {
    SessionData {
        meta: SessionMeta {
            session_id: sid.into(),
            title: "session_persist test".into(),
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

// ═══════════════════════════════════════════════════════════════════════
// Test 0 — F-012: run() fails fast when session was never pre-saved
// ═══════════════════════════════════════════════════════════════════════

// [边界] with_session_store 但 **不** 预 save session → run() 立即 Err，
// 而不是静默让每个 checkpoint snapshot NotFound（F-012 fail-fast）。
#[tokio::test]
async fn engine_fails_fast_on_unpreloaded_session() {
    let store = Arc::new(SqliteSessionStore::in_memory().await.unwrap());

    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("unused")))
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .build()
        .await
        .expect("harness build");

    // NOTE: deliberately do NOT pre-save the session.
    let err = h.run_react("hello").await.expect_err("run must fail fast");
    match err {
        RunError::SessionNotPreSaved { session_id } => {
            assert_eq!(session_id, h.engine.session_id());
        }
        other => panic!("expected SessionNotPreSaved, got {other:?}"),
    }

    // Nothing was persisted (no half-baked session row).
    assert!(store.list().await.expect("list").is_empty());
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — EngineBuilder.with_session_store + 1 round + load round-trip
// ═══════════════════════════════════════════════════════════════════════
// run_react 后 load(session_id) 应能取回 meta / state / last_checkpoint。
// 预 save session（因为 snapshot() 假设 session 已存在）。
#[tokio::test]
async fn engine_builder_installs_session_store_and_saves() {
    let store = Arc::new(SqliteSessionStore::in_memory().await.unwrap());

    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi back")))
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .build()
        .await
        .expect("harness build");

    // 预 save session（snapshot 依赖 session 已存在）
    let sid = h.engine.session_id().to_string();
    store
        .save(&make_initial_data(&sid))
        .await
        .expect("pre-save session");

    // 1 round 无 tool → 期望 3 Checkpoint fires: BeforeModelCall / AfterModelCall / RoundEnd
    let out = h.run_react("hello").await.expect("run");
    assert_eq!(out, "hi back");
    h.assert_state_messages(2);

    // snapshot 是 tokio::spawn 异步写——等一下
    tokio::time::sleep(Duration::from_millis(300)).await;

    // list 应有 1 个 session
    let list = store.list().await.expect("list");
    assert_eq!(list.len(), 1, "expected 1 session, got {:?}", list);
    let meta = &list[0];
    println!(
        "[persist] session_id={} title={} status={:?} round_count={} turn_count={}",
        meta.session_id, meta.title, meta.status, meta.round_count, meta.turn_count
    );

    // load 完整数据
    let data = store
        .load(&meta.session_id)
        .await
        .expect("load")
        .expect("session exists");
    assert_eq!(data.meta.session_id, meta.session_id);
    // state.messages 应有 2 条（user + assistant）
    assert_eq!(data.state.messages.len(), 2, "user + assistant");
    assert_eq!(data.state.messages[0].role, "user");
    assert_eq!(data.state.messages[1].role, "assistant");
    // last_checkpoint 应有（最后一个是 RoundEnd）
    let cp = data
        .last_checkpoint
        .expect("snapshot should have been written");
    println!(
        "[persist] last_checkpoint = {:?} turn_index={} captured_at={}",
        cp.checkpoint, cp.turn_index, cp.captured_at
    );
    assert_eq!(
        cp.checkpoint,
        arf_core::Checkpoint::RoundEnd,
        "expected last checkpoint to be RoundEnd"
    );
    // status 应该是 Interrupted（snapshot 路径强制 interrupted，session/lib.rs:412）
    assert_eq!(data.meta.status, SessionStatus::Interrupted);
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — session_id 默认 = engine.agent_id
// ═══════════════════════════════════════════════════════════════════════

// [构造] 不调 with_session_id → Engine 使用 engine.agent_id 当 session_id。
// load(agent_id) 应能取回。
#[tokio::test]
async fn session_id_defaults_to_agent_id() {
    let store = Arc::new(SqliteSessionStore::in_memory().await.unwrap());

    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("ok")))
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .build()
        .await
        .expect("harness build");

    // engine.agent_id 在 builder 内被设为 `engine/{provider}`（engine.rs:59）
    let expected_sid = h.engine.agent_id().to_string();
    println!("[persist] default session_id = {expected_sid}");

    // 预 save
    store
        .save(&make_initial_data(&expected_sid))
        .await
        .expect("pre-save");

    let _ = h.run_react("hi").await.expect("run");
    tokio::time::sleep(Duration::from_millis(300)).await;

    // load by agent_id 应当 Some
    let data = store
        .load(&expected_sid)
        .await
        .expect("load")
        .expect("default session id should be agent_id");
    assert_eq!(data.meta.session_id, expected_sid);
    assert_eq!(data.state.messages.len(), 2);
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — EngineBuilder::with_session_id("custom") 覆盖
// ═══════════════════════════════════════════════════════════════════════

// [方法] EngineBuilder::with_session_id("custom-session") → load("custom-session") 找到；
// load(agent_id) 返回 None。直接用 EngineBuilder 而非 harness——harness 未暴露
// with_session_id（test 1 / test 2 不需要它）。
#[tokio::test]
async fn session_id_override_via_builder() {
    use arf_engine::{AgentConfig, EngineBuilder, EngineConfig, ModelDecl};
    use arf_bus::Bus;
    use arf_model_adapter::ModelAdapterNode;
    use arf_core::NodeId;

    let store = Arc::new(SqliteSessionStore::in_memory().await.unwrap());
    let custom_sid = "my-custom-session-001";

    // 直接 build 一个 engine——不依赖 harness（harness 不暴露 with_session_id）
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    let _model_node = ModelAdapterNode::new(
        simple_mock("done"),
        &bus,
        NodeId::new("model/test"),
    )
    .await
    .expect("model node");

    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            max_turns: 10,
            ..Default::default()
        },
    };

    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .with_session_id(custom_sid)
        .build(cfg)
        .await
        .expect("engine build");

    // engine.session_id() 应是 custom
    assert_eq!(engine.session_id(), custom_sid);
    let agent_id = engine.agent_id().to_string();
    println!("[persist] custom session_id={custom_sid} agent_id={agent_id}");

    // 预 save
    store
        .save(&make_initial_data(custom_sid))
        .await
        .expect("pre-save custom");

    // 1 round
    let cancel = tokio_util::sync::CancellationToken::new();
    let mut state = arf_core::State::new();
    let out = tokio::time::timeout(
        Duration::from_secs(10),
        engine.run(&mut state, "test".into(), cancel),
    )
    .await
    .expect("timeout")
    .expect("run");
    assert_eq!(out, "done");
    tokio::time::sleep(Duration::from_millis(300)).await;

    // load by custom → Some
    let data = store
        .load(custom_sid)
        .await
        .expect("load custom")
        .expect("custom session should exist");
    assert_eq!(data.meta.session_id, custom_sid);

    // load by default agent_id → None
    let none_data = store.load(&agent_id).await.expect("load default");
    assert!(
        none_data.is_none(),
        "default agent_id should not have a session"
    );
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — config_snapshot 也被持久化
// ═══════════════════════════════════════════════════════════════════════

// [方法] SessionData.config_snapshot 字段应能通过 load 读出。
// 验证 SessionData 4 个字段（meta / state / last_checkpoint / config_snapshot）
// 在 load 端到端可见。
#[tokio::test]
async fn session_data_load_returns_all_4_fields() {
    let store = Arc::new(SqliteSessionStore::in_memory().await.unwrap());
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("ok")))
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .build()
        .await
        .expect("harness build");

    // 预 save（带 config_snapshot 内容）
    let sid = h.engine.session_id().to_string();
    let mut initial = make_initial_data(&sid);
    initial.config_snapshot = serde_json::json!({"model": "test-model", "k": "v"});
    store.save(&initial).await.expect("pre-save");

    let _ = h.run_react("hi").await.expect("run");
    tokio::time::sleep(Duration::from_millis(300)).await;

    let data = store.load(&sid).await.expect("load").expect("exists");
    // 4 字段全可见
    println!(
        "[persist] fields: meta={} state.messages={} last_checkpoint={:?} config_snapshot={}",
        data.meta.session_id,
        data.state.messages.len(),
        data.last_checkpoint.is_some(),
        data.config_snapshot
    );
    assert_eq!(data.meta.session_id, sid);
    assert!(!data.state.messages.is_empty());
    assert!(data.last_checkpoint.is_some());
    // config_snapshot 保留预 save 的值（snapshot() 不重写 config_snapshot）
    assert_eq!(data.config_snapshot, serde_json::json!({"model": "test-model", "k": "v"}));
}
