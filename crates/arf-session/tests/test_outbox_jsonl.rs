//! Task 17/19 — JsonlSessionStore peer outbox tests.
//!
//! Migrated to the unified `events` JSONL format (Task 19): tests use
//! `record_event` + `pending_outbound` directly. The legacy record_peer_* /
//! pending_peer_messages wrappers still work but go through record_event.

use arf_session::{Event, JsonlSessionStore, PendingOutbound, SessionStore};
use chrono::Utc;
use serde_json::json;
use uuid::Uuid;

// [方法] sent 但无 reply → pending 非空
#[tokio::test]
async fn pending_derives_unsent_messages() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

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
}

// [方法] sent + reply → pending 空
#[tokio::test]
async fn pending_excludes_messags_with_reply() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let cid = Uuid::new_v4();
    store.record_event("A", &Event::OutboundSent {
        msg_type: "peer_message".into(),
        correlation_id: cid,
        attempt: 1,
        target: vec!["engine-B".into()],
        payload: json!({"content": "hi"}),
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("A", &Event::InboundReply {
        msg_type: "peer_reply".into(),
        correlation_id: cid,
        source: "engine-B".into(),
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let pending = store.pending_outbound("A").await.unwrap();
    assert!(pending.is_empty(), "已收到 reply 不应再 pending");
}

// [方法] 多 sent 部分 reply → pending 仅未完成
#[tokio::test]
async fn pending_partial_completion_only_unsent() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let mut sent_ids = Vec::new();
    for _ in 0..3 {
        let cid = Uuid::new_v4();
        sent_ids.push(cid);
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
        correlation_id: sent_ids[0],
        source: "B".into(),
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let pending = store.pending_outbound("A").await.unwrap();
    assert_eq!(pending.len(), 2);
    let pending_ids: std::collections::HashSet<_> =
        pending.iter().map(|p| p.correlation_id).collect();
    assert!(pending_ids.contains(&sent_ids[1]));
    assert!(pending_ids.contains(&sent_ids[2]));
}

// [持久化] event kind 写入 JSONL 格式正确（嵌套 event 字段）
#[tokio::test]
async fn record_event_persists_to_jsonl() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let cid = Uuid::new_v4();
    store.record_event("A", &Event::OutboundSent {
        msg_type: "peer_message".into(),
        correlation_id: cid,
        attempt: 1,
        target: vec!["engine-B".into()],
        payload: json!({"content": "x"}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"kind\":\"event\""), "raw: {content}");
    assert!(content.contains("\"event\":{"), "raw: {content}");
    assert!(content.contains("\"kind\":\"outbound_sent\""), "raw: {content}");
    assert!(content.contains("\"msg_type\":\"peer_message\""), "raw: {content}");
    assert!(content.contains(&cid.to_string()), "raw: {content}");
}

// [边界] record_event(InboundReply) 写入 JSONL
#[tokio::test]
async fn record_reply_persists_to_jsonl() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let cid = Uuid::new_v4();
    store.record_event("A", &Event::InboundReply {
        msg_type: "peer_reply".into(),
        correlation_id: cid,
        source: "engine-B".into(),
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"kind\":\"inbound_reply\""), "raw: {content}");
    assert!(content.contains("\"msg_type\":\"peer_reply\""), "raw: {content}");
    assert!(content.contains(&cid.to_string()), "raw: {content}");
    assert!(content.contains("\"source\":\"engine-B\""), "raw: {content}");
}

// [边界] 同 cid 多次 resend → pending 取 max attempt
#[tokio::test]
async fn pending_takes_max_attempt_for_same_cid() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let cid = Uuid::new_v4();
    for attempt in [1u32, 2, 3] {
        store.record_event("A", &Event::OutboundSent {
            msg_type: "peer_message".into(),
            correlation_id: cid,
            attempt,
            target: vec!["B".into()],
            payload: json!({"attempt": attempt}),
            captured_at: Utc::now(),
        }).await.unwrap();
    }

    let pending = store.pending_outbound("A").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].correlation_id, cid);
    assert_eq!(pending[0].attempt, 3, "应取最大 attempt");
}

// [边界] 不存在的 session → pending 空 vec
#[tokio::test]
async fn pending_nonexistent_session_empty() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());
    let pending = store.pending_outbound("never_existed").await.unwrap();
    assert!(pending.is_empty());
}

// [边界] 损坏行 → 跳过不影响后续
#[tokio::test]
async fn pending_skips_corrupt_lines() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let cid = Uuid::new_v4();
    store.record_event("A", &Event::OutboundSent {
        msg_type: "peer_message".into(),
        correlation_id: cid,
        attempt: 1,
        target: vec!["B".into()],
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();

    // 手动追加坏行（forward-recovery: JSON parse failure → skip line）
    let path = tmp.path().join("events.A.jsonl");
    let mut content = std::fs::read_to_string(&path).unwrap();
    content.push_str("this is not json\n");
    content.push_str("{\"kind\":\"event\",\"event\":{\"kind\":\"outbound_sent\",\"msg_type\":\"peer_message\",\"correlation_id\":\"not-a-uuid\",\"attempt\":1,\"target\":[],\"payload\":null,\"captured_at\":\"2026-01-01T00:00:00Z\"}}\n");
    std::fs::write(&path, content).unwrap();

    let pending = store.pending_outbound("A").await.unwrap();
    assert_eq!(pending.len(), 1, "损坏行应被跳过，正常行仍解析");
    assert_eq!(pending[0].correlation_id, cid);
}