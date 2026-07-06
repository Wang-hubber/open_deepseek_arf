//! Task 17/19 — SqliteSessionStore event log tests.
//!
//! Migrated to the unified async outbox API (Task 19): tests now exercise
//! `record_event` + `pending_outbound` directly. The 6 record_* methods are
//! thin wrappers and are no longer tested at the integration level (covered
//! by serde roundtrip tests on Event in event.rs).

use arf_session::{Event, PendingOutbound, SessionStore, SqliteSessionStore};
use chrono::Utc;
use serde_json::json;
use uuid::Uuid;

// [方法] record_event(OutboundSent) 后 pending_outbound 可见
#[tokio::test]
async fn sqlite_record_and_pending() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let cid = Uuid::new_v4();
    store.record_event("A", &Event::OutboundSent {
        msg_type: "peer_message".into(),
        correlation_id: cid,
        attempt: 1,
        target: vec!["engine-B".into()],
        payload: json!({"content": "hi"}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let pending = store.pending_outbound("A").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].correlation_id, cid);
    assert_eq!(pending[0].msg_type, "peer_message");
    assert_eq!(pending[0].target_nodes, vec!["engine-B".to_string()]);
    assert_eq!(pending[0].attempt, 1);
}

// [方法] sent + reply → pending 空
#[tokio::test]
async fn sqlite_pending_after_reply_empty() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let cid = Uuid::new_v4();
    store.record_event("A", &Event::OutboundSent {
        msg_type: "peer_message".into(),
        correlation_id: cid,
        attempt: 1,
        target: vec!["B".into()],
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("A", &Event::InboundReply {
        msg_type: "peer_reply".into(),
        correlation_id: cid,
        source: "B".into(),
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();

    assert!(store.pending_outbound("A").await.unwrap().is_empty());
}

// [方法] 多 sent 部分 reply → pending 仅未完成
#[tokio::test]
async fn sqlite_pending_partial_completion() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let mut ids = Vec::new();
    for _ in 0..3 {
        let cid = Uuid::new_v4();
        ids.push(cid);
        store.record_event("A", &Event::OutboundSent {
            msg_type: "peer_message".into(),
            correlation_id: cid,
            attempt: 1,
            target: vec!["B".into()],
            payload: json!({}),
            captured_at: Utc::now(),
        }).await.unwrap();
    }
    store.record_event("A", &Event::InboundReply {
        msg_type: "peer_reply".into(),
        correlation_id: ids[0],
        source: "B".into(),
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("A", &Event::InboundReply {
        msg_type: "peer_reply".into(),
        correlation_id: ids[2],
        source: "B".into(),
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let pending = store.pending_outbound("A").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].correlation_id, ids[1]);
}

// [边界] 同 cid 多次 resend → pending 取 max attempt
#[tokio::test]
async fn sqlite_pending_max_attempt() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let cid = Uuid::new_v4();
    for attempt in [1u32, 3, 2] {
        store.record_event("A", &Event::OutboundSent {
            msg_type: "peer_message".into(),
            correlation_id: cid,
            attempt,
            target: vec!["B".into()],
            payload: json!({}),
            captured_at: Utc::now(),
        }).await.unwrap();
    }

    let pending = store.pending_outbound("A").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].attempt, 3);
}

// [边界] 不存在 session → pending 空
#[tokio::test]
async fn sqlite_pending_nonexistent_session_empty() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    let pending = store.pending_outbound("nope").await.unwrap();
    assert!(pending.is_empty());
}

// [边界] session 间隔离 — A 的 pending 不影响 B
#[tokio::test]
async fn sqlite_pending_isolated_per_session() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let cid = Uuid::new_v4();
    store.record_event("A", &Event::OutboundSent {
        msg_type: "peer_message".into(),
        correlation_id: cid,
        attempt: 1,
        target: vec!["B".into()],
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let pending_b = store.pending_outbound("B").await.unwrap();
    assert!(pending_b.is_empty(), "session B 不应有 pending");

    let pending_a = store.pending_outbound("A").await.unwrap();
    assert_eq!(pending_a.len(), 1);
}