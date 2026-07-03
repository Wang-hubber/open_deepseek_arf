//! session_multi_id.rs — Phase 9 task 9.10.4
//!
//! 探查跨 session_id 行为：单 SqliteSessionStore 持多 session，list / load /
//! delete / snapshot 各自独立，跨 session_id 不串台。
//!
//! 3 test cases:
//! 1. multiple_sessions_isolated_in_one_store — 3 session 各自 save/load 互不影响
//! 2. delete_one_session_keeps_others — delete 1 个不影响其他
//! 3. snapshot_other_session_does_not_touch — snapshot 1 个不影响其他
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.10.4.md`（独立文件，独立 commit）。

mod common;

use arf_session::{
    CheckpointSnapshot, SessionData, SessionMeta, SessionStatus, SessionStore, SqliteSessionStore,
};
use arf_core::{Checkpoint, ModelMessage, State};
use chrono::Utc;

fn make_data(sid: &str, title: &str, msg: &str) -> SessionData {
    let mut state = State::new();
    state.push_message(ModelMessage::new("user", msg));
    state.over_view.round_count = 1;
    state.over_view.turn_count = 1;
    SessionData {
        meta: SessionMeta {
            session_id: sid.into(),
            title: title.into(),
            created_at: Utc::now(),
            updated_at: Utc::now(),
            round_count: 1,
            turn_count: 1,
            status: SessionStatus::Active,
            current_round: None,
        },
        state,
        last_checkpoint: None,
        config_snapshot: serde_json::json!({}),
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — 多 session 在一个 store 中互不串台
// ═══════════════════════════════════════════════════════════════════════

// [方法] 单 store 持 3 session（不同 sid + title + 内容）→ 各自 save 互不影响；
// load(各 sid) 各自返回各自数据；list 3 个。
#[tokio::test]
async fn multiple_sessions_isolated_in_one_store() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    store
        .save(&make_data("sess-a", "Session A", "msg A"))
        .await
        .expect("save A");
    store
        .save(&make_data("sess-b", "Session B", "msg B"))
        .await
        .expect("save B");
    store
        .save(&make_data("sess-c", "Session C", "msg C"))
        .await
        .expect("save C");

    // list 全部 3 个
    let list = store.list().await.expect("list");
    assert_eq!(list.len(), 3, "should have 3 sessions");

    // 各自 load 互不影响
    let a = store.load("sess-a").await.expect("load a").expect("exists");
    let b = store.load("sess-b").await.expect("load b").expect("exists");
    let c = store.load("sess-c").await.expect("load c").expect("exists");

    assert_eq!(a.meta.title, "Session A");
    assert_eq!(b.meta.title, "Session B");
    assert_eq!(c.meta.title, "Session C");
    assert_eq!(a.state.messages[0].content, "msg A");
    assert_eq!(b.state.messages[0].content, "msg B");
    assert_eq!(c.state.messages[0].content, "msg C");
    println!("[multi] 3 sessions isolated: titles + messages all distinct ✓");

    // load 不存在的 sid → None（不与其他 session 串台）
    let none_data = store.load("sess-doesnt-exist").await.expect("load none");
    assert!(none_data.is_none());
    println!("[multi] load nonexistent → None ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — delete 1 个不影响其他 + ON DELETE CASCADE
// ═══════════════════════════════════════════════════════════════════════

// [方法] 3 session → save B + snapshot B（写 B 的 checkpoints）→
// delete B → load(B) None、load(A/C) Some、list 2 个；
// 验证 B 的 checkpoints 也被级联删除（frame/lib.rs:238 `ON DELETE CASCADE`）。
// 用 file 模式让 second-conn 能访问同一 DB（in_memory 是 per-connection）。
#[tokio::test]
async fn delete_one_session_keeps_others() {
    let tmp = tempfile::tempdir().expect("tmpdir");
    let db_path = tmp.path().join("delete_cascade.db");
    let store = SqliteSessionStore::new(&db_path).await.unwrap();

    store.save(&make_data("sess-a", "A", "a")).await.unwrap();
    store.save(&make_data("sess-b", "B", "b")).await.unwrap();
    store.save(&make_data("sess-c", "C", "c")).await.unwrap();

    // snapshot B（确保 B 的 checkpoints 表有行）
    let mut state_b = State::new();
    state_b.push_message(ModelMessage::new("user", "b"));
    let cp = CheckpointSnapshot::new(Checkpoint::AfterModelCall, 1);
    store.snapshot("sess-b", &state_b, &cp).await.expect("snapshot B");

    // 验证 B 存在
    let pre = store.load("sess-b").await.expect("load B pre").expect("exists");
    assert!(pre.last_checkpoint.is_some());
    println!("[multi] pre-delete: B exists with last_checkpoint ✓");

    // delete B
    store.delete("sess-b").await.expect("delete B");

    // load B → None
    let post = store.load("sess-b").await.expect("load B post");
    assert!(post.is_none(), "deleted session should not load");
    println!("[multi] post-delete: load(B) = None ✓");

    // load A / C → Some
    assert!(store.load("sess-a").await.expect("load A").is_some());
    assert!(store.load("sess-c").await.expect("load C").is_some());
    println!("[multi] post-delete: load(A/C) = Some ✓");

    // list → 2
    let list = store.list().await.expect("list");
    assert_eq!(list.len(), 2, "should have 2 sessions after delete B");
    let ids: Vec<&str> = list.iter().map(|m| m.session_id.as_str()).collect();
    assert!(ids.contains(&"sess-a"));
    assert!(ids.contains(&"sess-c"));
    assert!(!ids.contains(&"sess-b"));
    println!("[multi] post-delete: list 2 sessions (A, C) ✓");

    // 验证 B 的 checkpoints 也被级联删除
    use rusqlite::Connection;
    let conn = Connection::open(store.path()).expect("open shared db");
    let b_cp_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM checkpoints WHERE session_id = 'sess-b'",
            [],
            |r| r.get(0),
        )
        .expect("count B checkpoints");
    assert_eq!(b_cp_count, 0, "B's checkpoints should be CASCADE deleted");
    println!("[multi] B's checkpoints CASCADE deleted (0 rows) ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — snapshot 1 个 session 不影响其他 session
// ═══════════════════════════════════════════════════════════════════════

// [方法] 3 session save 后，snapshot A → load(A).state 反映 snapshot 时
// 的 state；load(B/C).state **未**被 snapshot 改动（独立隔离）。
#[tokio::test]
async fn snapshot_other_session_does_not_touch() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    store.save(&make_data("sess-a", "A", "a-orig")).await.unwrap();
    store.save(&make_data("sess-b", "B", "b-orig")).await.unwrap();
    store.save(&make_data("sess-c", "C", "c-orig")).await.unwrap();

    // snapshot A with a different state (push extra message)
    let mut state_a_new = State::new();
    state_a_new.push_message(ModelMessage::new("user", "a-orig"));
    state_a_new.push_message(ModelMessage::new("assistant", "a-new-response"));
    state_a_new.over_view.round_count = 2;
    let cp = CheckpointSnapshot::new(Checkpoint::AfterModelCall, 2);
    store.snapshot("sess-a", &state_a_new, &cp).await.expect("snapshot A");

    // load A: state 应反映 snapshot（2 messages, round_count=2）
    let a = store.load("sess-a").await.expect("load A").expect("exists");
    assert_eq!(a.state.messages.len(), 2, "A should have 2 messages after snapshot");
    assert_eq!(a.state.over_view.round_count, 2);
    assert!(a.last_checkpoint.is_some());
    println!("[multi] A snapshot applied: messages=2, round_count=2 ✓");

    // load B: state 应保持 save 时的状态（1 message, round_count=1）
    let b = store.load("sess-b").await.expect("load B").expect("exists");
    assert_eq!(b.state.messages.len(), 1, "B should have 1 message");
    assert_eq!(b.state.messages[0].content, "b-orig");
    assert_eq!(b.state.over_view.round_count, 1);
    assert!(b.last_checkpoint.is_none(), "B should have no checkpoint");
    println!("[multi] B untouched: messages=1, no checkpoint ✓");

    // load C: 同 B
    let c = store.load("sess-c").await.expect("load C").expect("exists");
    assert_eq!(c.state.messages.len(), 1);
    assert_eq!(c.state.messages[0].content, "c-orig");
    assert_eq!(c.state.over_view.round_count, 1);
    assert!(c.last_checkpoint.is_none());
    println!("[multi] C untouched: messages=1, no checkpoint ✓");
}
