//! Task 17 — JsonlSessionStore peer outbox tests.

use arf_session::{JsonlSessionStore, PendingPeerMessage, SessionStore};
use chrono::Utc;
use uuid::Uuid;

// [方法] sent 但无 reply → pending 非空
#[tokio::test]
async fn pending_derives_unsent_messages() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

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
}

// [方法] sent + reply → pending 空
#[tokio::test]
async fn pending_excludes_messags_with_reply() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let sent = PendingPeerMessage {
        correlation_id: Uuid::new_v4(),
        target_session: "B".into(),
        target_node: "engine-B".into(),
        payload: serde_json::json!({"content": "hi"}),
        sent_at: Utc::now(),
        attempt: 1,
    };
    store.record_peer_message_sent("A", &sent).await.unwrap();
    store
        .record_peer_reply_received("A", sent.correlation_id, "engine-B")
        .await
        .unwrap();

    let pending = store.pending_peer_messages("A").await.unwrap();
    assert!(pending.is_empty(), "已收到 reply 不应再 pending");
}

// [方法] 多 sent 部分 reply → pending 仅未完成
#[tokio::test]
async fn pending_partial_completion_only_unsent() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let mut sent_ids = Vec::new();
    for _ in 0..3 {
        let s = PendingPeerMessage {
            correlation_id: Uuid::new_v4(),
            target_session: "B".into(),
            target_node: "engine-B".into(),
            payload: serde_json::json!({}),
            sent_at: Utc::now(),
            attempt: 1,
        };
        sent_ids.push(s.correlation_id);
        store.record_peer_message_sent("A", &s).await.unwrap();
    }
    store
        .record_peer_reply_received("A", sent_ids[0], "engine-B")
        .await
        .unwrap();

    let pending = store.pending_peer_messages("A").await.unwrap();
    assert_eq!(pending.len(), 2);
    let pending_ids: std::collections::HashSet<_> =
        pending.iter().map(|p| p.correlation_id).collect();
    assert!(pending_ids.contains(&sent_ids[1]));
    assert!(pending_ids.contains(&sent_ids[2]));
}

// [持久化] event kind 写入 JSONL 格式正确
#[tokio::test]
async fn record_event_persists_to_jsonl() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let sent = PendingPeerMessage {
        correlation_id: Uuid::new_v4(),
        target_session: "B".into(),
        target_node: "engine-B".into(),
        payload: serde_json::json!({"content": "x"}),
        sent_at: Utc::now(),
        attempt: 1,
    };
    store.record_peer_message_sent("A", &sent).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"kind\":\"event\""));
    assert!(content.contains("\"event_type\":\"peer_message_sent\""));
    assert!(content.contains(&sent.correlation_id.to_string()));
}

// [边界] record_peer_reply_received 写入 JSONL
#[tokio::test]
async fn record_reply_persists_to_jsonl() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let cid = Uuid::new_v4();
    store
        .record_peer_reply_received("A", cid, "engine-B")
        .await
        .unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"event_type\":\"peer_reply_received\""));
    assert!(content.contains(&cid.to_string()));
    assert!(content.contains("\"source\":\"engine-B\""));
}

// [边界] 同 cid 多次 resend → pending 取 max attempt
#[tokio::test]
async fn pending_takes_max_attempt_for_same_cid() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let cid = Uuid::new_v4();
    // 模拟首发 (attempt=1) 和 resend (attempt=2)
    for attempt in [1u32, 2, 3] {
        let s = PendingPeerMessage {
            correlation_id: cid,
            target_session: "B".into(),
            target_node: "engine-B".into(),
            payload: serde_json::json!({"attempt": attempt}),
            sent_at: Utc::now(),
            attempt,
        };
        store.record_peer_message_sent("A", &s).await.unwrap();
    }

    let pending = store.pending_peer_messages("A").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].correlation_id, cid);
    assert_eq!(pending[0].attempt, 3, "应取最大 attempt");
}

// [边界] 不存在的 session → pending 空 vec
#[tokio::test]
async fn pending_nonexistent_session_empty() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());
    let pending = store.pending_peer_messages("never_existed").await.unwrap();
    assert!(pending.is_empty());
}

// [边界] 损坏行 → 跳过不影响后续
#[tokio::test]
async fn pending_skips_corrupt_lines() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let cid = Uuid::new_v4();
    let s = PendingPeerMessage {
        correlation_id: cid,
        target_session: "B".into(),
        target_node: "engine-B".into(),
        payload: serde_json::json!({}),
        sent_at: Utc::now(),
        attempt: 1,
    };
    store.record_peer_message_sent("A", &s).await.unwrap();

    // 手动追加坏行
    let path = tmp.path().join("events.A.jsonl");
    let mut content = std::fs::read_to_string(&path).unwrap();
    content.push_str("this is not json\n");
    content.push_str("{\"kind\":\"event\",\"event_type\":\"peer_message_sent\",\"correlation_id\":\"not-a-uuid\"}\n");
    std::fs::write(&path, content).unwrap();

    let pending = store.pending_peer_messages("A").await.unwrap();
    assert_eq!(pending.len(), 1, "损坏行应被跳过，正常行仍解析");
    assert_eq!(pending[0].correlation_id, cid);
}