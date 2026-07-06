//! JSONL session store — append-only per-session file.

use std::path::PathBuf;
use async_trait::async_trait;
use chrono::Utc;
use tokio::io::AsyncWriteExt;
use uuid::Uuid;

use crate::{
    CheckpointSnapshot, PendingPeerMessage, SessionData, SessionError, SessionMeta, SessionStatus,
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
        // Snapshot lines (Task 1) intentionally omit a `data` payload —
        // embedding SessionData is a downstream design decision. If the
        // chosen snapshot has no usable data, fall back to the latest save
        // (or return None if neither has data).
        let has_data = |v: &serde_json::Value| -> bool {
            v.get("data").map(|d| !d.is_null()).unwrap_or(false)
        };
        let chosen = match latest_snap.as_ref() {
            Some(s) if has_data(s) => Some(s.clone()),
            Some(s) => {
                tracing::warn!(
                    session_id = %session_id,
                    "snapshot line lacks data payload; falling back to latest save \
                     (a future task will embed SessionData in snapshot lines)"
                );
                if has_data(latest_save.as_ref().unwrap_or(&serde_json::Value::Null)) {
                    latest_save.clone()
                } else {
                    None
                }
            }
            None => latest_save.clone(),
        };
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

    // ── Task 17: peer_message outbox (spec §2.4) ──────────────────────

    async fn record_peer_message_sent(
        &self,
        session_id: &str,
        record: &PendingPeerMessage,
    ) -> Result<(), SessionError> {
        let path = self.file_path(session_id);
        if let Some(p) = path.parent() {
            tokio::fs::create_dir_all(p).await?;
        }
        let line = serde_json::json!({
            "kind": "event",
            "event_type": "peer_message_sent",
            "at": Utc::now(),
            "correlation_id": record.correlation_id.to_string(),
            "target_session": record.target_session,
            "target_node": record.target_node,
            "payload": record.payload,
            "attempt": record.attempt,
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

    async fn record_peer_reply_received(
        &self,
        session_id: &str,
        correlation_id: Uuid,
        source: &str,
    ) -> Result<(), SessionError> {
        let path = self.file_path(session_id);
        if let Some(p) = path.parent() {
            tokio::fs::create_dir_all(p).await?;
        }
        let line = serde_json::json!({
            "kind": "event",
            "event_type": "peer_reply_received",
            "at": Utc::now(),
            "correlation_id": correlation_id.to_string(),
            "source": source,
        });
        let mut f = tokio::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .await?;
        f.write_all(line.to_string().as_bytes()).await?;
        f.write_all(b"\n").await?;
        // fsync: 否则下次重启会误重发
        f.sync_all().await?;
        Ok(())
    }

    async fn pending_peer_messages(
        &self,
        session_id: &str,
    ) -> Result<Vec<PendingPeerMessage>, SessionError> {
        let path = self.file_path(session_id);
        if !path.exists() {
            return Ok(Vec::new());
        }
        let content = tokio::fs::read_to_string(&path).await?;

        // 第一遍：收集 sent 的 cid → PendingPeerMessage（attempt 取最大）
        // 第二遍：扣除有 reply 的 cid
        let mut sent: std::collections::HashMap<Uuid, PendingPeerMessage> =
            std::collections::HashMap::new();
        let mut replied: std::collections::HashSet<Uuid> = std::collections::HashSet::new();

        for line in content.lines() {
            if line.is_empty() {
                continue;
            }
            let v: serde_json::Value = match serde_json::from_str(line) {
                Ok(v) => v,
                Err(_) => continue, // 容错：跳过损坏行
            };
            let kind = v.get("kind").and_then(|k| k.as_str()).unwrap_or("");
            if kind != "event" {
                continue;
            }
            let event_type = v.get("event_type").and_then(|k| k.as_str()).unwrap_or("");

            let cid_str = match v.get("correlation_id").and_then(|c| c.as_str()) {
                Some(s) => s,
                None => continue,
            };
            let cid = match Uuid::parse_str(cid_str) {
                Ok(u) => u,
                Err(_) => continue,
            };

            match event_type {
                "peer_message_sent" => {
                    let attempt =
                        v.get("attempt").and_then(|a| a.as_u64()).unwrap_or(1) as u32;
                    let sent_at = v
                        .get("at")
                        .and_then(|a| a.as_str())
                        .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
                        .map(|d| d.with_timezone(&Utc))
                        .unwrap_or_else(Utc::now);
                    let pm = PendingPeerMessage {
                        correlation_id: cid,
                        target_session: v
                            .get("target_session")
                            .and_then(|s| s.as_str())
                            .unwrap_or("")
                            .to_string(),
                        target_node: v
                            .get("target_node")
                            .and_then(|s| s.as_str())
                            .unwrap_or("")
                            .to_string(),
                        payload: v
                            .get("payload")
                            .cloned()
                            .unwrap_or(serde_json::Value::Null),
                        sent_at,
                        attempt,
                    };
                    sent.entry(cid)
                        .and_modify(|e| {
                            if pm.attempt > e.attempt {
                                *e = pm.clone();
                            }
                        })
                        .or_insert(pm);
                }
                "peer_reply_received" => {
                    replied.insert(cid);
                }
                _ => {}
            }
        }

        // 派生：sent - replied
        let mut pending: Vec<PendingPeerMessage> = sent
            .into_iter()
            .filter(|(cid, _)| !replied.contains(cid))
            .map(|(_, pm)| pm)
            .collect();
        // 按 sent_at 升序（先发先重发）
        pending.sort_by(|a, b| a.sent_at.cmp(&b.sent_at));
        Ok(pending)
    }
}
