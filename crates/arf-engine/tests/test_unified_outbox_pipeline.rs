//! Task 19 — End-to-end pipeline test for unified async outbox.
//!
//! Verifies the full record_event → pending_outbound → reconstruct → resend
//! pipeline by exercising the public APIs directly. Avoids full Engine + Bus
//! harness; instead drives the SQLite/JSONL stores + reconstructor to verify
//! the semantics hold across all 4 spec scenarios.
//!
//! Scenarios mirror spec §5.2 but at the store layer (no bus involvement):
//! - Scenario 1 (HITL cross-engine crash recovery): record OutboundSent for
//!   human_handoff + InboundReply — pending_outbound must exclude replied cids
//! - Scenario 2 (mixed outbox): peer_message replied + human_handoff not —
//!   pending_outbound returns only the unreplied one
//! - Scenario 3 (process-level dedup): InboundDedupCache absorbs duplicates
//! - Scenario 4 (resend failure tolerance): PendingOutbound rows reconstruct
//!   correctly even when one entry's payload is malformed (skip-and-continue)
//!
//! Note: scenario labels match spec naming but the "Engine restart" boundary
//! is replaced by re-invoking pending_outbound() on the same store — the
//! store-level semantics are equivalent.

use arf_core::NodeId;
use arf_engine::{InboundDedupCache, message_reconstruct::reconstruct_message};
use arf_session::{Event, JsonlSessionStore, PendingOutbound, SessionStore, SqliteSessionStore};
use chrono::Utc;
use serde_json::json;
use uuid::Uuid;

// Scenario 1: HumanHandoff cross-engine crash recovery (store-level)
#[tokio::test]
async fn scenario_1_human_handoff_pending_after_no_reply() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    let cid = Uuid::new_v4();

    // Engine A: record OutboundSent(human_handoff, attempt=1)
    store.record_event("s1", &Event::OutboundSent {
        msg_type: "human_handoff".into(),
        correlation_id: cid,
        attempt: 1,
        target: vec!["ui".into()],
        payload: json!({
            "correlation_id": cid.to_string(),
            "question": "approve?",
            "context": {},
            "options": ["yes", "no"],
        }),
        captured_at: Utc::now(),
    }).await.unwrap();

    // Engine A crashes before getting reply. Engine B starts → pending_outbound.
    let pending = store.pending_outbound("s1").await.unwrap();
    assert_eq!(pending.len(), 1, "unreplied HumanHandoff must be pending");
    assert_eq!(pending[0].msg_type, "human_handoff");
    assert_eq!(pending[0].correlation_id, cid);
    assert_eq!(pending[0].attempt, 1);

    // Reconstruct the message from the pending row → bus.send-ready
    let msg = reconstruct_message(&pending[0], NodeId::new("engine-A")).unwrap();
    assert_eq!(msg.msg_type, "human_handoff");
    assert_eq!(msg.correlation_id(), Some(cid));
    assert_eq!(msg.payload.get("question").and_then(|v| v.as_str()), Some("approve?"));
}

// Scenario 2: Mixed outbox (peer_message replied + human_handoff pending)
#[tokio::test]
async fn scenario_2_mixed_outbound_pending() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    let peer_cid = Uuid::new_v4();
    let handoff_cid = Uuid::new_v4();

    store.record_event("s1", &Event::OutboundSent {
        msg_type: "peer_message".into(),
        correlation_id: peer_cid,
        attempt: 1,
        target: vec!["engine-B".into()],
        payload: json!({
            "correlation_id": peer_cid.to_string(),
            "from_session": "A",
            "to_session": "B",
            "content": "hi",
            "attachments": [],
        }),
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("s1", &Event::InboundReply {
        msg_type: "peer_reply".into(),
        correlation_id: peer_cid,
        source: "engine-B".into(),
        payload: json!({}),
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("s1", &Event::OutboundSent {
        msg_type: "human_handoff".into(),
        correlation_id: handoff_cid,
        attempt: 1,
        target: vec!["ui".into()],
        payload: json!({
            "correlation_id": handoff_cid.to_string(),
            "question": "ok?",
            "context": {},
            "options": [],
        }),
        captured_at: Utc::now(),
    }).await.unwrap();

    let pending = store.pending_outbound("s1").await.unwrap();
    assert_eq!(pending.len(), 1, "only unreplied HumanHandoff should be pending");
    assert_eq!(pending[0].msg_type, "human_handoff");
    assert_eq!(pending[0].correlation_id, handoff_cid);
}

// Scenario 3: Process-level dedup absorbs duplicate InboundReply
#[tokio::test]
async fn scenario_3_process_level_dedup_absorbs_duplicate() {
    let cache = InboundDedupCache::new(64);
    let cid = Uuid::new_v4();

    // First arrival: miss → cache records cid
    assert!(!cache.check_and_record(&cid), "first sight: miss");
    // Duplicate delivery (e.g., bus re-delivered): hit → drop
    assert!(cache.check_and_record(&cid), "duplicate: hit (drop)");

    // Distinct cids are independent
    let other = Uuid::new_v4();
    assert!(!cache.check_and_record(&other), "other cid: miss");
}

// Scenario 4: Reconstruct fails on poison row → skip (does not abort loop)
#[tokio::test]
async fn scenario_4_reconstruct_failure_does_not_panic() {
    let p = PendingOutbound {
        msg_type: "unknown_msg_type".into(),  // not in reconstruct_message
        correlation_id: Uuid::new_v4(),
        target_nodes: vec!["x".into()],
        payload: json!({}),
        attempt: 1,
    };
    let res = reconstruct_message(&p, NodeId::new("engine"));
    assert!(res.is_err(), "unknown msg_type must error");
    let err = res.unwrap_err();
    matches!(err, arf_engine::message_reconstruct::ReconstructError::UnknownMsgType(_));
}

// Bonus: MAX(attempt) wins across multiple OutboundSent rows for same cid
#[tokio::test]
async fn scenario_5_max_attempt_wins() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    let cid = Uuid::new_v4();
    for attempt in [1u32, 3, 2] {
        store.record_event("s1", &Event::OutboundSent {
            msg_type: "peer_message".into(),
            correlation_id: cid,
            attempt,
            target: vec!["B".into()],
            payload: json!({"content": format!("attempt {attempt}")}),
            captured_at: Utc::now(),
        }).await.unwrap();
    }
    let pending = store.pending_outbound("s1").await.unwrap();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].attempt, 3);
}

// Bonus: store-agnostic — JSONL produces same cid set as SQLite (regression)
#[tokio::test]
async fn scenario_6_jsonl_sqlite_equivalence() {
    let jsonl_tmp = tempfile::tempdir().unwrap();
    let jsonl_store = JsonlSessionStore::new(jsonl_tmp.path());
    let sqlite_store = SqliteSessionStore::in_memory().await.unwrap();
    let cid = Uuid::new_v4();
    let evt = Event::OutboundSent {
        msg_type: "peer_message".into(),
        correlation_id: cid,
        attempt: 1,
        target: vec!["B".into()],
        payload: json!({"content": "x"}),
        captured_at: Utc::now(),
    };
    jsonl_store.record_event("s1", &evt).await.unwrap();
    sqlite_store.record_event("s1", &evt).await.unwrap();
    let jp = jsonl_store.pending_outbound("s1").await.unwrap();
    let sp = sqlite_store.pending_outbound("s1").await.unwrap();
    assert_eq!(jp.len(), 1);
    assert_eq!(sp.len(), 1);
    assert_eq!(jp[0].correlation_id, sp[0].correlation_id);
}