use arf_session::{JsonlSessionStore, SessionStore, SessionData, SessionMeta, CheckpointSnapshot, SessionStatus};
use arf_core::{Checkpoint, ModelMessage, State};

// ── Task 1 ──────────────────────────────────────────────────────────

#[tokio::test]
async fn snapshot_writes_jsonl_line() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());
    let snap = CheckpointSnapshot::new(Checkpoint::AfterToolExec, 0);
    store.snapshot("s1", &State::new(), &snap).await.unwrap();

    let path = tmp.path().join("events.s1.jsonl");
    let content = std::fs::read_to_string(&path).unwrap();
    assert!(content.contains("\"kind\":\"snapshot\""));
    assert_eq!(content.lines().count(), 1);
}

// ── Task 2 ──────────────────────────────────────────────────────────

#[tokio::test]
async fn save_load_round_trip() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    // Adapt to actual SessionData shape: `state` is `State`, not `serde_json::Value`.
    let mut state = State::new();
    state.push_message(ModelMessage::new("user", "hi"));

    let data = SessionData {
        meta: SessionMeta {
            session_id: "s1".into(),
            title: "round-trip".into(),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
            round_count: 0,
            turn_count: 1,
            status: SessionStatus::Active,
            current_round: None,
        },
        state,
        last_checkpoint: None,
        config_snapshot: serde_json::json!({}),
        model_params: Default::default(),
    };
    store.save(&data).await.unwrap();

    let loaded = store.load("s1").await.unwrap().unwrap();
    assert_eq!(loaded.meta.session_id, "s1");
    assert_eq!(loaded.state.messages.len(), 1);
    assert_eq!(loaded.state.messages[0].content, "hi");
    assert_eq!(loaded.config_snapshot, serde_json::json!({}));
}

#[tokio::test]
async fn load_missing_returns_none() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());
    assert!(store.load("nonexistent").await.unwrap().is_none());
}

#[tokio::test]
async fn list_scans_events_files() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    // Two saves → two events.<id>.jsonl files
    for (id, ts) in [("a", 0i64), ("b", 1i64)] {
        let mut state = State::new();
        state.push_message(ModelMessage::new("user", id));
        let data = SessionData {
            meta: SessionMeta {
                session_id: id.into(),
                title: id.into(),
                created_at: chrono::Utc::now() + chrono::Duration::seconds(ts),
                updated_at: chrono::Utc::now() + chrono::Duration::seconds(ts),
                round_count: 0,
                turn_count: 1,
                status: SessionStatus::Active,
                current_round: None,
            },
            state,
            last_checkpoint: None,
            config_snapshot: serde_json::json!({}),
            model_params: Default::default(),
        };
        store.save(&data).await.unwrap();
    }

    let list = store.list().await.unwrap();
    assert_eq!(list.len(), 2);
    // Sorted by updated_at DESC: "b" is newer than "a"
    assert_eq!(list[0].session_id, "b");
    assert_eq!(list[1].session_id, "a");
}

#[tokio::test]
async fn delete_removes_file() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let data = SessionData {
        meta: SessionMeta::new("sdel", "to delete"),
        state: State::new(),
        last_checkpoint: None,
        config_snapshot: serde_json::json!({}),
        model_params: Default::default(),
    };
    store.save(&data).await.unwrap();
    assert!(tmp.path().join("events.sdel.jsonl").exists());

    store.delete("sdel").await.unwrap();
    assert!(!tmp.path().join("events.sdel.jsonl").exists());
    assert!(store.load("sdel").await.unwrap().is_none());
}

#[tokio::test]
async fn delete_missing_is_ok() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());
    // Deleting a non-existent session must not error (idempotent).
    store.delete("nope").await.unwrap();
}

// ── Task 2 fix: snapshot-only load ──────────────────────────────────

#[tokio::test]
async fn load_snapshot_only_returns_none() {
    // Per Task 1, snapshot lines intentionally omit a `data` payload
    // (embedding SessionData is a downstream design decision). So a
    // file containing only `snapshot` lines must not error on load;
    // it should return Ok(None) per the brief's "snapshot 优先；否则
    // 用最新的 save" — implicitly if neither has data, return None.
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let snap = CheckpointSnapshot::new(Checkpoint::AfterToolExec, 3);
    store.snapshot("snap_only", &State::new(), &snap).await.unwrap();

    let result = store.load("snap_only").await.unwrap();
    assert!(result.is_none(), "snapshot-only file should return None, not error");
}
