use arf_session::{JsonlSessionStore, SessionStore, CheckpointSnapshot};
use arf_core::Checkpoint;
use arf_core::State;

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
