//! JSONL session store — append-only per-session file.

use std::path::PathBuf;
use async_trait::async_trait;
use chrono::Utc;
use tokio::io::AsyncWriteExt;

use crate::{
    CheckpointSnapshot, SessionData, SessionError, SessionMeta, SessionStatus,
    SessionStore, SnapshotEffects,
};
use arf_core::State;

pub struct JsonlSessionStore {
    base_dir: PathBuf,
}

impl JsonlSessionStore {
    pub fn new(base_dir: impl Into<PathBuf>) -> Self {
        Self { base_dir: base_dir.into() }
    }
    fn file_path(&self, session_id: &str) -> PathBuf {
        self.base_dir.join(format!("events.{session_id}.jsonl"))
    }
}

#[async_trait]
impl SessionStore for JsonlSessionStore {
    async fn list(&self) -> Result<Vec<SessionMeta>, SessionError> { Ok(vec![]) }
    async fn load(&self, _: &str) -> Result<Option<SessionData>, SessionError> { Ok(None) }
    async fn save(&self, _: &SessionData) -> Result<(), SessionError> { Ok(()) }
    async fn delete(&self, _: &str) -> Result<(), SessionError> { Ok(()) }

    async fn snapshot(
        &self,
        session_id: &str,
        _state: &State,
        snap: &CheckpointSnapshot,
    ) -> Result<SnapshotEffects, SessionError> {
        let path = self.file_path(session_id);
        if let Some(p) = path.parent() {
            tokio::fs::create_dir_all(p).await?;
        }
        let line = serde_json::json!({
            "kind": "snapshot",
            "at": Utc::now(),
            "checkpoint": snap.checkpoint,
            "turn_index": snap.turn_index,
        });
        let mut f = tokio::fs::OpenOptions::new().create(true).append(true).open(&path).await?;
        f.write_all(line.to_string().as_bytes()).await?;
        f.write_all(b"\n").await?;
        f.sync_all().await?;
        Ok(SnapshotEffects {
            checkpoint_written: Utc::now(),
            state_updated: true,
            updated_at: Utc::now(),
            status_forced: SessionStatus::Interrupted,
        })
    }
}
