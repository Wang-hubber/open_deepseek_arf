//! Task 19 — SqliteSessionStore unified event log tests.
//!
//! Exercises record_event + pending_outbound semantics beyond what
//! test_outbox_sqlite covers (msg_type-agnostic, FIFO ordering,
//! MAX(attempt) per cid).

use arf_session::{Event, PendingOutbound, SessionStore, SqliteSessionStore};
use chrono::{Duration as ChronoDuration, Utc};
use serde_json::json;
use uuid::Uuid;

// [方法] record_event(OutboundSent{msg_type="human_handoff"}) round-trip
#[tokio::test]
async fn record_event_outbound_sent_roundtrip() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    let cid = Uuid::new_v4();
    store.record_event("s1", &Event::OutboundSent {
        msg_type: "human_handoff".into(),
        correlation_id: cid,
        attempt: 1,
        target: vec!["ui".into()],
        payload: json!({"question": "ok?"}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let pending = store.pending_outbound("s1").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].msg_type, "human_handoff");
    assert_eq!(pending[0].correlation_id, cid);
    assert_eq!(pending[0].attempt, 1);
}

// [方法] pending_outbound 排除已 reply 的
#[tokio::test]
async fn pending_outbound_excludes_replied() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    let cid_a = Uuid::new_v4();
    let cid_b = Uuid::new_v4();

    store.record_event("s1", &Event::OutboundSent {
        msg_type: "peer_message".into(), correlation_id: cid_a,
        attempt: 1, target: vec!["B".into()], payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("s1", &Event::InboundReply {
        msg_type: "peer_reply".into(), correlation_id: cid_a,
        source: "B".into(), payload: json!({}), captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("s1", &Event::OutboundSent {
        msg_type: "human_handoff".into(), correlation_id: cid_b,
        attempt: 1, target: vec!["ui".into()], payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let pending = store.pending_outbound("s1").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].correlation_id, cid_b);
}

// [边界] pending_outbound 使用 MAX(attempt) per cid
#[tokio::test]
async fn pending_outbound_uses_max_attempt() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    let cid = Uuid::new_v4();

    store.record_event("s1", &Event::OutboundSent {
        msg_type: "peer_message".into(), correlation_id: cid,
        attempt: 1, target: vec!["B".into()], payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("s1", &Event::OutboundSent {
        msg_type: "peer_message".into(), correlation_id: cid,
        attempt: 2, target: vec!["B".into()], payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let pending = store.pending_outbound("s1").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].attempt, 2);
}

// [边界] pending_outbound 按 captured_at 升序（FIFO）
#[tokio::test]
async fn pending_outbound_orders_by_captured_at() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    let cid_a = Uuid::new_v4();
    let cid_b = Uuid::new_v4();
    let earlier = Utc::now() - ChronoDuration::seconds(5);

    store.record_event("s1", &Event::OutboundSent {
        msg_type: "peer_message".into(), correlation_id: cid_b,
        attempt: 1, target: vec!["B".into()], payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("s1", &Event::OutboundSent {
        msg_type: "peer_message".into(), correlation_id: cid_a,
        attempt: 1, target: vec!["B".into()], payload: json!({}),
        captured_at: earlier,
    }).await.unwrap();

    let pending = store.pending_outbound("s1").await.unwrap();
    assert_eq!(pending[0].correlation_id, cid_a, "earlier first");
    assert_eq!(pending[1].correlation_id, cid_b);
}

// [方法] record_peer_message_sent 默认 wrapper 仍能写入新 events 表
#[tokio::test]
async fn record_peer_message_sent_wrapper_writes_new_schema() {
    use arf_session::PendingPeerMessage;

    let store = SqliteSessionStore::in_memory().await.unwrap();
    let cid = Uuid::new_v4();
    let sent = PendingPeerMessage {
        correlation_id: cid,
        target_session: "B".into(),
        target_node: "engine-B".into(),
        payload: json!({"content": "x"}),
        sent_at: Utc::now(),
        attempt: 1,
    };
    store.record_peer_message_sent("A", &sent).await.unwrap();

    let pending = store.pending_outbound("A").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].msg_type, "peer_message");
    assert_eq!(pending[0].correlation_id, cid);
}