//! JSONL session store — append-only per-session file.

use std::path::PathBuf;
use async_trait::async_trait;
use chrono::Utc;
use tokio::io::AsyncWriteExt;
use uuid::Uuid;

use crate::{
    CheckpointSnapshot, Event, PendingOutbound, SessionData, SessionError,
    SessionMeta, SessionStatus, SessionStore, SnapshotEffects,
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
        // Prefer the latest save line — it carries the full SessionData
        // (meta + config + model_params). Snapshot lines (Task 19) embed
        // `data: State` for debugging / forward-compat but load() doesn't
        // reconstruct SessionData from State alone. Fall back to snapshot
        // only if no save line exists AND snapshot.data deserializes as a
        // SessionData (legacy format from before Task 19).
        if let Some(s) = latest_save.as_ref() {
            let data: SessionData = serde_json::from_value(s["data"].clone())?;
            return Ok(Some(data));
        }
        if let Some(s) = latest_snap.as_ref() {
            // Legacy snapshot-with-SessionData path: try to deserialize.
            // If snapshot.data is a State (Task 19 format), this fails and
            // we return None — caller should re-save the session.
            match serde_json::from_value::<SessionData>(s["data"].clone()) {
                Ok(data) => return Ok(Some(data)),
                Err(_) => {
                    tracing::warn!(
                        session_id = %session_id,
                        "snapshot line data is not SessionData (likely State from Task 19); \
                         no save line found — returning None"
                    );
                    return Ok(None);
                }
            }
        }
        Ok(None)
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
        state: &State,
        snap: &CheckpointSnapshot,
    ) -> Result<SnapshotEffects, SessionError> {
        let path = self.file_path(session_id);
        if let Some(p) = path.parent() {
            tokio::fs::create_dir_all(p).await?;
        }
        // Embed State as `data` so load() can prefer snapshot-with-data over
        // the latest save line (spec §2.3 — fixes prior gap where snapshot
        // lines lacked a data payload).
        let line = serde_json::json!({
            "kind": "snapshot",
            "at": Utc::now(),
            "checkpoint": snap.checkpoint,
            "turn_index": snap.turn_index,
            "data": state,
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

    // ── Task 19: Unified event log (record_event + pending_outbound) ──
    //
    // Trait defaults route record_peer_message_sent, record_*_end through
    // record_event. We implement record_event + pending_outbound here.

    async fn record_event(
        &self,
        session_id: &str,
        event: &Event,
    ) -> Result<(), SessionError> {
        let path = self.file_path(session_id);
        if let Some(p) = path.parent() {
            tokio::fs::create_dir_all(p).await?;
        }
        let line = serde_json::json!({
            "kind": "event",
            "event": event,
        });
        let mut f = tokio::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .await?;
        f.write_all(line.to_string().as_bytes()).await?;
        f.write_all(b"\n").await?;
        // fsync: 崩溃后必须能恢复（spec §2.3）
        f.sync_all().await?;
        Ok(())
    }

    async fn pending_outbound(
        &self,
        session_id: &str,
    ) -> Result<Vec<PendingOutbound>, SessionError> {
        use std::collections::{HashMap, HashSet};
        let path = self.file_path(session_id);
        if !path.exists() {
            return Ok(Vec::new());
        }
        let content = tokio::fs::read_to_string(&path).await?;

        let mut max_attempt: HashMap<Uuid, u32> = HashMap::new();
        let mut first_seen: HashMap<Uuid, chrono::DateTime<Utc>> = HashMap::new();
        let mut last_payload: HashMap<Uuid, serde_json::Value> = HashMap::new();
        let mut last_target: HashMap<Uuid, Vec<String>> = HashMap::new();
        let mut last_msg_type: HashMap<Uuid, String> = HashMap::new();
        let mut replied: HashSet<Uuid> = HashSet::new();

        for line in content.lines() {
            if line.is_empty() {
                continue;
            }
            let val: serde_json::Value = match serde_json::from_str(line) {
                Ok(v) => v,
                Err(_) => continue,
            };
            if val.get("kind").and_then(|v| v.as_str()) != Some("event") {
                continue;
            }
            let evt: Event = match serde_json::from_value(
                val.get("event").cloned().unwrap_or(serde_json::Value::Null),
            ) {
                Ok(e) => e,
                Err(_) => continue,
            };
            match evt {
                Event::OutboundSent {
                    msg_type,
                    correlation_id,
                    attempt,
                    target,
                    payload,
                    captured_at,
                } => {
                    let entry = max_attempt.entry(correlation_id).or_insert(0);
                    if attempt > *entry {
                        *entry = attempt;
                    }
                    first_seen.entry(correlation_id).or_insert(captured_at);
                    last_payload.insert(correlation_id, payload);
                    last_target.insert(correlation_id, target);
                    last_msg_type.insert(correlation_id, msg_type);
                }
                Event::InboundReply { correlation_id, .. } => {
                    replied.insert(correlation_id);
                }
                _ => {}
            }
        }

        let mut out: Vec<PendingOutbound> = max_attempt
            .into_iter()
            .filter(|(cid, _)| !replied.contains(cid))
            .map(|(cid, attempt)| PendingOutbound {
                msg_type: last_msg_type.remove(&cid).unwrap_or_default(),
                correlation_id: cid,
                target_nodes: last_target.remove(&cid).unwrap_or_default(),
                payload: last_payload.remove(&cid).unwrap_or(serde_json::Value::Null),
                attempt,
            })
            .collect();
        // Order by first_seen captured_at ascending (FIFO)
        out.sort_by_key(|p| first_seen.get(&p.correlation_id).copied().unwrap_or_else(Utc::now));
        Ok(out)
    }
}
