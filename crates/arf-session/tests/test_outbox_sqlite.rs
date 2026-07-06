//! Task 17 — SqliteSessionStore peer outbox tests.

use arf_session::{PendingPeerMessage, SessionStore, SqliteSessionStore};
use chrono::Utc;
use uuid::Uuid;

// [方法] Sqlite record + pending 派生
#[tokio::test]
async fn sqlite_record_and_pending() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let sent = PendingPeerMessage {
        correlation_id: Uuid::new_v4(),
        target_session: "B".into(),
        target_node: "engine-B".into(),
        payload: serde_json::json!({"content": "hi"}),
        sent_at: Utc::now(),
        attempt: 1,
    };
    store.record_peer_message_sent("A", &sent).await.unwrap();

    let pending = store.pending_peer_messages("A").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].correlation_id, sent.correlation_id);
    assert_eq!(pending[0].target_session, "B");
    assert_eq!(pending[0].target_node, "engine-B");
    assert_eq!(pending[0].attempt, 1);
}

// [方法] Sqlite sent + reply → pending 空
#[tokio::test]
async fn sqlite_pending_after_reply_empty() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let sent = PendingPeerMessage {
        correlation_id: Uuid::new_v4(),
        target_session: "B".into(),
        target_node: "engine-B".into(),
        payload: serde_json::json!({}),
        sent_at: Utc::now(),
        attempt: 1,
    };
    store.record_peer_message_sent("A", &sent).await.unwrap();
    store
        .record_peer_reply_received("A", sent.correlation_id, "engine-B")
        .await
        .unwrap();

    assert!(store.pending_peer_messages("A").await.unwrap().is_empty());
}

// [方法] 多 sent 部分 reply → pending 仅未完成
#[tokio::test]
async fn sqlite_pending_partial_completion() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let mut ids = Vec::new();
    for _ in 0..3 {
        let s = PendingPeerMessage {
            correlation_id: Uuid::new_v4(),
            target_session: "B".into(),
            target_node: "engine-B".into(),
            payload: serde_json::json!({}),
            sent_at: Utc::now(),
            attempt: 1,
        };
        ids.push(s.correlation_id);
        store.record_peer_message_sent("A", &s).await.unwrap();
    }
    store
        .record_peer_reply_received("A", ids[0], "engine-B")
        .await
        .unwrap();
    store
        .record_peer_reply_received("A", ids[2], "engine-B")
        .await
        .unwrap();

    let pending = store.pending_peer_messages("A").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].correlation_id, ids[1]);
}

// [边界] 同 cid 多次 resend → pending 取 max attempt
#[tokio::test]
async fn sqlite_pending_max_attempt() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let cid = Uuid::new_v4();
    for attempt in [1u32, 3, 2] {
        let s = PendingPeerMessage {
            correlation_id: cid,
            target_session: "B".into(),
            target_node: "engine-B".into(),
            payload: serde_json::json!({}),
            sent_at: Utc::now(),
            attempt,
        };
        store.record_peer_message_sent("A", &s).await.unwrap();
    }

    let pending = store.pending_peer_messages("A").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].attempt, 3);
}

// [边界] 不存在 session → pending 空
#[tokio::test]
async fn sqlite_pending_nonexistent_session_empty() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    let pending = store.pending_peer_messages("nope").await.unwrap();
    assert!(pending.is_empty());
}

// [边界] session 间隔离 — A 的 pending 不影响 B
#[tokio::test]
async fn sqlite_pending_isolated_per_session() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    let sent_a = PendingPeerMessage {
        correlation_id: Uuid::new_v4(),
        target_session: "B".into(),
        target_node: "engine-B".into(),
        payload: serde_json::json!({}),
        sent_at: Utc::now(),
        attempt: 1,
    };
    store.record_peer_message_sent("A", &sent_a).await.unwrap();

    let pending_b = store.pending_peer_messages("B").await.unwrap();
    assert!(pending_b.is_empty(), "session B 不应有 pending");

    let pending_a = store.pending_peer_messages("A").await.unwrap();
    assert_eq!(pending_a.len(), 1);
}