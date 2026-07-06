//! Task 19 — JSONL ↔ SQLite pending_outbound semantic equivalence test.

use arf_session::{Event, JsonlSessionStore, SessionStore, SqliteSessionStore};
use chrono::Utc;
use serde_json::json;
use std::collections::HashSet;
use uuid::Uuid;

// [兼容] JSONL pending_outbound 与 SQLite 语义等价（同输入，cid 集合一致）
#[tokio::test]
async fn pending_outbound_equivalence_with_sqlite() {
    let jsonl_tmp = tempfile::tempdir().unwrap();
    let jsonl_store = JsonlSessionStore::new(jsonl_tmp.path());
    let sqlite_store = SqliteSessionStore::in_memory().await.unwrap();

    let cid_a = Uuid::new_v4();
    let cid_b = Uuid::new_v4();

    let evts = vec![
        Event::OutboundSent {
            msg_type: "peer_message".into(), correlation_id: cid_a,
            attempt: 1, target: vec!["B".into()], payload: json!({}),
            captured_at: Utc::now(),
        },
        Event::InboundReply {
            msg_type: "peer_reply".into(), correlation_id: cid_a,
            source: "B".into(), payload: json!({}), captured_at: Utc::now(),
        },
        Event::OutboundSent {
            msg_type: "human_handoff".into(), correlation_id: cid_b,
            attempt: 1, target: vec!["ui".into()], payload: json!({}),
            captured_at: Utc::now(),
        },
    ];
    for e in &evts {
        jsonl_store.record_event("s1", e).await.unwrap();
        sqlite_store.record_event("s1", e).await.unwrap();
    }

    let j = jsonl_store.pending_outbound("s1").await.unwrap();
    let s = sqlite_store.pending_outbound("s1").await.unwrap();
    let jc: HashSet<_> = j.iter().map(|p| p.correlation_id).collect();
    let sc: HashSet<_> = s.iter().map(|p| p.correlation_id).collect();
    assert_eq!(jc, sc);
    assert_eq!(jc.len(), 1);
    assert!(jc.contains(&cid_b));
}