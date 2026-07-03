//! session_custom_store.rs — Phase 9 task 9.10.5
//!
//! 探查自定义 SessionStore impl trait 的端到端能力。
//! 让 app 用 `impl SessionStore for InMemoryStore` 写自己的 store，
//! EngineBuilder::with_session_store(Arc<dyn SessionStore>) 接受任意 impl。
//!
//! 3 test cases:
//! 1. in_memory_store_impl_trait — InMemoryStore（Mutex<HashMap>）impl 5 方法端到端
//! 2. custom_store_with_engine_round_trip — Engine + InMemoryStore + 1 round
//! 3. custom_recording_store_counts_snapshot_calls — RecordingStore 记录 snapshot 调用次数
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.10.5.md`（独立文件，独立 commit）。

mod common;

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use arf_session::{
    CheckpointSnapshot, SessionData, SessionError, SessionStore,
};
use async_trait::async_trait;
use common::harness::{E2EHarness, ProviderKind};
use common::provider::simple_mock;

// ═══════════════════════════════════════════════════════════════════════
// 自定义 InMemoryStore — 纯 HashMap，零外部依赖
// ═══════════════════════════════════════════════════════════════════════

/// In-memory SessionStore. Holds sessions in a HashMap and checkpoints
/// in a Vec (按 captured_at 倒序遍历找 last）。
struct InMemoryStore {
    sessions: Mutex<HashMap<String, SessionData>>,
    checkpoints: Mutex<Vec<(String, CheckpointSnapshot)>>,
}

impl InMemoryStore {
    fn new() -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            checkpoints: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl SessionStore for InMemoryStore {
    async fn list(&self) -> Result<Vec<arf_session::SessionMeta>, SessionError> {
        let map = self.sessions.lock().unwrap();
        let mut out: Vec<_> = map.values().map(|d| d.meta.clone()).collect();
        // 模拟 SqliteSessionStore 的 ORDER BY updated_at DESC
        out.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
        Ok(out)
    }

    async fn load(&self, session_id: &str) -> Result<Option<SessionData>, SessionError> {
        let map = self.sessions.lock().unwrap();
        let Some(data) = map.get(session_id) else {
            return Ok(None);
        };
        let mut out = data.clone();
        // 取 last checkpoint（最新 captured_at）
        let cps = self.checkpoints.lock().unwrap();
        let cp = cps
            .iter()
            .filter(|(sid, _)| sid == session_id)
            .max_by_key(|(_, c)| c.captured_at)
            .map(|(_, c)| c.clone());
        out.last_checkpoint = cp;
        Ok(Some(out))
    }

    async fn save(&self, data: &SessionData) -> Result<(), SessionError> {
        let mut map = self.sessions.lock().unwrap();
        map.insert(data.meta.session_id.clone(), data.clone());
        Ok(())
    }

    async fn delete(&self, session_id: &str) -> Result<(), SessionError> {
        let mut map = self.sessions.lock().unwrap();
        if map.remove(session_id).is_none() {
            return Err(SessionError::NotFound(session_id.into()));
        }
        let mut cps = self.checkpoints.lock().unwrap();
        cps.retain(|(sid, _)| sid != session_id);
        Ok(())
    }

    async fn snapshot(
        &self,
        session_id: &str,
        state: &arf_core::State,
        snapshot: &CheckpointSnapshot,
    ) -> Result<(), SessionError> {
        let mut map = self.sessions.lock().unwrap();
        let Some(data) = map.get_mut(session_id) else {
            return Err(SessionError::NotFound(session_id.into()));
        };
        // 模拟 SqliteSessionStore 的 UPDATE sessions.state_json 副作用（session/lib.rs:409-414）
        data.state = state.clone();
        data.meta.updated_at = chrono::Utc::now();
        drop(map);
        let mut cps = self.checkpoints.lock().unwrap();
        // 模拟 SqliteSessionStore 的 `INSERT OR REPLACE` 语义（同 captured_at 覆盖）
        cps.retain(|(sid, c)| !(sid == session_id && c.captured_at == snapshot.captured_at));
        cps.push((session_id.to_string(), snapshot.clone()));
        Ok(())
    }
}

/// 预 save 一个 session（不依赖 Engine；模拟 interrupt.rs:255-270 模式）
fn make_data(sid: &str) -> SessionData {
    SessionData {
        meta: arf_session::SessionMeta {
            session_id: sid.into(),
            title: "custom test".into(),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
            round_count: 0,
            turn_count: 0,
            status: arf_session::SessionStatus::Active,
            current_round: None,
        },
        state: arf_core::State::new(),
        last_checkpoint: None,
        config_snapshot: serde_json::json!({}),
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — InMemoryStore impl trait 5 方法端到端
// ═══════════════════════════════════════════════════════════════════════

// [方法] E (Extensible)：app 自定义 InMemoryStore impl SessionStore 5 方法
// （list/load/save/delete/snapshot）—— 端到端能工作。
#[tokio::test]
async fn in_memory_store_impl_trait() {
    let store = InMemoryStore::new();

    // save 3 session
    store.save(&make_data("s-1")).await.expect("save 1");
    store.save(&make_data("s-2")).await.expect("save 2");
    store.save(&make_data("s-3")).await.expect("save 3");

    // list → 3
    let list = store.list().await.expect("list");
    assert_eq!(list.len(), 3);
    println!("[custom] list 3 sessions ✓");

    // load → 各自
    let d1 = store.load("s-1").await.expect("load 1").expect("exists");
    assert_eq!(d1.meta.session_id, "s-1");
    let d_none = store.load("nope").await.expect("load nope");
    assert!(d_none.is_none());
    println!("[custom] load each sid ✓");

    // snapshot → checkpoints 累计
    let mut s = arf_core::State::new();
    s.push_message(arf_core::ModelMessage::new("user", "hi"));
    let cp1 = CheckpointSnapshot::new(arf_core::Checkpoint::BeforeModelCall, 1);
    let cp2 = CheckpointSnapshot::new(arf_core::Checkpoint::AfterModelCall, 1);
    store.snapshot("s-1", &s, &cp1).await.expect("snapshot 1");
    store.snapshot("s-1", &s, &cp2).await.expect("snapshot 2");
    let d1_after = store.load("s-1").await.expect("load 1 after").expect("exists");
    assert!(d1_after.last_checkpoint.is_some());
    assert_eq!(d1_after.last_checkpoint.unwrap().checkpoint, arf_core::Checkpoint::AfterModelCall);
    println!("[custom] snapshot 累计 + load 返回 latest checkpoint ✓");

    // delete s-2 → s-1 + s-3 仍存在；checkpoints 隔离
    store.delete("s-2").await.expect("delete 2");
    assert!(store.load("s-2").await.expect("load 2 post").is_none());
    assert!(store.load("s-1").await.expect("load 1 post").is_some());
    assert!(store.load("s-3").await.expect("load 3 post").is_some());
    println!("[custom] delete 单 session + 其他不受影响 ✓");

    // delete 不存在 → NotFound
    let err = store.delete("nope").await.unwrap_err();
    match err {
        SessionError::NotFound(_) => {}
        other => panic!("expected NotFound, got {other:?}"),
    }
    println!("[custom] delete nonexistent → NotFound ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — Engine + InMemoryStore 端到端
// ═══════════════════════════════════════════════════════════════════════

// [方法] EngineBuilder::with_session_store(Arc<InMemoryStore>) + 1 round →
// InMemoryStore 的 snapshot() 被调多次，load() 返回 latest snapshot。
#[tokio::test]
async fn custom_store_with_engine_round_trip() {
    let store = Arc::new(InMemoryStore::new());
    let store_dyn: Arc<dyn SessionStore> = store.clone();

    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("hi")))
        .with_session_store(store_dyn)
        .build()
        .await
        .expect("harness build");

    // 预 save
    let sid = h.engine.session_id().to_string();
    store.save(&make_data(&sid)).await.expect("pre-save");

    let out = h.run_react("hello").await.expect("run");
    assert_eq!(out, "hi");

    // 等 snapshot
    tokio::time::sleep(Duration::from_millis(300)).await;

    // load 端到端 OK
    let loaded = store.load(&sid).await.expect("load").expect("exists");
    assert_eq!(loaded.state.messages.len(), 2, "user + assistant");
    assert!(loaded.last_checkpoint.is_some());
    println!(
        "[custom/engine] load OK: messages={}, last_checkpoint={:?}",
        loaded.state.messages.len(),
        loaded.last_checkpoint.as_ref().unwrap().checkpoint
    );
    // 最后一次 snapshot 应该是 RoundEnd
    assert_eq!(
        loaded.last_checkpoint.as_ref().unwrap().checkpoint,
        arf_core::Checkpoint::RoundEnd
    );

    // list 也应能看到该 session
    let list = store.list().await.expect("list");
    assert_eq!(list.len(), 1);
    assert_eq!(list[0].session_id, sid);
    println!("[custom/engine] list 1 session with sid {sid} ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — RecordingStore 记录 snapshot 调用次数
// ═══════════════════════════════════════════════════════════════════════

// [方法] 自定义 RecordingStore 用 atomic counter 记录 snapshot 调用次数。
// Engine + RecordingStore + 1 round 无 tool → 期望 snapshot 调 3 次
// (BMC, AMC, RE)。验证 trait 行为 contract。
struct RecordingStore {
    inner: InMemoryStore,
    snapshot_count: Arc<Mutex<usize>>,
    /// captured checkpoint kinds
    captured_kinds: Arc<Mutex<Vec<arf_core::Checkpoint>>>,
}

impl RecordingStore {
    fn new() -> Self {
        Self {
            inner: InMemoryStore::new(),
            snapshot_count: Arc::new(Mutex::new(0)),
            captured_kinds: Arc::new(Mutex::new(Vec::new())),
        }
    }
    fn counts(&self) -> (usize, Vec<arf_core::Checkpoint>) {
        let n = *self.snapshot_count.lock().unwrap();
        let k = self.captured_kinds.lock().unwrap().clone();
        (n, k)
    }
}

#[async_trait]
impl SessionStore for RecordingStore {
    async fn list(&self) -> Result<Vec<arf_session::SessionMeta>, SessionError> {
        self.inner.list().await
    }
    async fn load(&self, sid: &str) -> Result<Option<SessionData>, SessionError> {
        self.inner.load(sid).await
    }
    async fn save(&self, data: &SessionData) -> Result<(), SessionError> {
        self.inner.save(data).await
    }
    async fn delete(&self, sid: &str) -> Result<(), SessionError> {
        self.inner.delete(sid).await
    }
    async fn snapshot(
        &self,
        sid: &str,
        state: &arf_core::State,
        snapshot: &CheckpointSnapshot,
    ) -> Result<(), SessionError> {
        *self.snapshot_count.lock().unwrap() += 1;
        self.captured_kinds.lock().unwrap().push(snapshot.checkpoint);
        self.inner.snapshot(sid, state, snapshot).await
    }
}

#[tokio::test]
async fn custom_recording_store_counts_snapshot_calls() {
    let store = Arc::new(RecordingStore::new());
    let store_dyn: Arc<dyn SessionStore> = store.clone();

    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("ok")))
        .with_session_store(store_dyn)
        .build()
        .await
        .expect("harness build");

    let sid = h.engine.session_id().to_string();
    store.save(&make_data(&sid)).await.expect("pre-save");

    let _ = h.run_react("hi").await.expect("run");
    tokio::time::sleep(Duration::from_millis(300)).await;

    let (n, kinds) = store.counts();
    println!("[custom/rec] snapshot calls = {n}, kinds = {kinds:?}");
    // 1 round 无 tool → 3 fires
    assert_eq!(n, 3, "1 round no tool should fire 3 snapshots");
    assert_eq!(kinds.len(), 3);
    assert_eq!(kinds[0], arf_core::Checkpoint::BeforeModelCall);
    assert_eq!(kinds[1], arf_core::Checkpoint::AfterModelCall);
    assert_eq!(kinds[2], arf_core::Checkpoint::RoundEnd);
    println!("[custom/rec] 3 fires in order BMC → AMC → RE ✓");
}
