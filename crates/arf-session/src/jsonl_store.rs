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
    async fn list(&self) -> Result<Vec<SessionMeta>, SessionError> {
        let mut out = vec![];
        let mut rd = tokio::fs::read_dir(&self.base_dir).await?;
        while let Some(entry) = rd.next_entry().await? {
            let name = entry.file_name().to_string_lossy().to_string();
            if let Some(sid) = name.strip_prefix("events.").and_then(|s| s.strip_suffix(".jsonl")) {
                if let Ok(Some(d)) = self.load(sid).await {
                    out.push(d.meta);
                }
            }
        }
        out.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
        Ok(out)
    }

    async fn load(&self, session_id: &str) -> Result<Option<SessionData>, SessionError> {
        let path = self.file_path(session_id);
        if !path.exists() {
            return Ok(None);
        }
        let content = tokio::fs::read_to_string(&path).await?;
        let mut latest_snap: Option<serde_json::Value> = None;
        let mut latest_save: Option<serde_json::Value> = None;
        for line in content.lines() {
            if line.is_empty() {
                continue;
            }
            let v: serde_json::Value = serde_json::from_str(line)?;
            match v.get("kind").and_then(|k| k.as_str()) {
                Some("snapshot") => latest_snap = Some(v),
                Some("save") => latest_save = Some(v),
                _ => {}
            }
        }
        // snapshot 优先；否则用最新的 save
        let chosen = latest_snap.or(latest_save);
        let Some(v) = chosen else { return Ok(None) };
        let data: SessionData = serde_json::from_value(v["data"].clone())?;
        Ok(Some(data))
    }

    async fn save(&self, data: &SessionData) -> Result<(), SessionError> {
        let path = self.file_path(&data.meta.session_id);
        if let Some(p) = path.parent() {
            tokio::fs::create_dir_all(p).await?;
        }
        let line = serde_json::json!({
            "kind": "save",
            "at": Utc::now(),
            "data": data,
        });
        let mut f = tokio::fs::OpenOptions::new().create(true).append(true).open(&path).await?;
        f.write_all(line.to_string().as_bytes()).await?;
        f.write_all(b"\n").await?;
        f.sync_all().await?;
        Ok(())
    }

    async fn delete(&self, session_id: &str) -> Result<(), SessionError> {
        let path = self.file_path(session_id);
        if path.exists() {
            tokio::fs::remove_file(&path).await?;
        }
        Ok(())
    }

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
