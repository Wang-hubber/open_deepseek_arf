//! ARF Session persistence (Phase 8 task F4).
//!
//! Provides:
//! - [`SessionStore`] trait — abstract persistence interface
//! - [`SqliteSessionStore`] — SQLite-backed implementation
//! - [`SessionMeta`] / [`SessionData`] / [`CheckpointSnapshot`] — types
//!
//! The Engine integrates via `EngineBuilder::with_session_store` (Phase 8 task F5).
//! Sessions are scoped by `session_id`; a single SQLite database holds N sessions
//! across multiple `chat()` rounds and across process restarts.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use arf_core::{Checkpoint, ModelMessage, NodeId, State, WaitEvent};
use async_trait::async_trait;
use chrono::{DateTime, Utc};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::sync::Mutex;

// ── Public types ─────────────────────────────────────────────────────

/// Session lifecycle status (Phase 8 task F4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum SessionStatus {
    /// In-progress; engine may have a pending checkpoint.
    Active,
    /// Round completed cleanly (model_response had no tool_calls).
    Completed,
    /// Process exited with an in-flight turn (Engine.run() interrupted).
    Interrupted,
}

impl SessionStatus {
    fn as_str(&self) -> &'static str {
        match self {
            SessionStatus::Active => "active",
            SessionStatus::Completed => "completed",
            SessionStatus::Interrupted => "interrupted",
        }
    }

    fn from_str(s: &str) -> Result<Self, SessionError> {
        match s {
            "active" => Ok(SessionStatus::Active),
            "completed" => Ok(SessionStatus::Completed),
            "interrupted" => Ok(SessionStatus::Interrupted),
            other => Err(SessionError::Corrupt(format!(
                "unknown session status: {other}"
            ))),
        }
    }
}

/// Session metadata — what's shown in the CLI's session list.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionMeta {
    pub session_id: String,
    pub title: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub round_count: usize,
    pub turn_count: usize,
    pub status: SessionStatus,
    /// Optional reference to the most recent unfinished round.
    pub current_round: Option<usize>,
}

impl SessionMeta {
    pub fn new(session_id: impl Into<String>, title: impl Into<String>) -> Self {
        let now = Utc::now();
        Self {
            session_id: session_id.into(),
            title: title.into(),
            created_at: now,
            updated_at: now,
            round_count: 0,
            turn_count: 0,
            status: SessionStatus::Active,
            current_round: None,
        }
    }
}

/// A captured checkpoint — written after each of the 5 Checkpoint positions
/// in `Engine.run()`. Allows replay of in-flight turns on restart.
///
/// `PartialEq` is not derived because `WaitEvent` doesn't implement it.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckpointSnapshot {
    pub checkpoint: Checkpoint,
    pub turn_index: usize,
    /// Messages that have been pushed to `state.messages` since the last
    /// checkpoint, but not yet committed. On replay, these are re-pushed
    /// and the in-flight turn re-executed.
    pub pending_messages: Vec<ModelMessage>,
    /// Outstanding WaitEvents for the in-flight query (model_call or tool_exec).
    pub wait_events: Vec<WaitEvent>,
    pub captured_at: DateTime<Utc>,
    /// Optional sub-task DAG snapshot (for subagent / DAG replay).
    /// We keep this as a JSON Value to avoid forcing arf-state types here.
    pub tasks_json: serde_json::Value,
}

impl CheckpointSnapshot {
    pub fn new(checkpoint: Checkpoint, turn_index: usize) -> Self {
        Self {
            checkpoint,
            turn_index,
            pending_messages: Vec::new(),
            wait_events: Vec::new(),
            captured_at: Utc::now(),
            tasks_json: serde_json::Value::Null,
        }
    }
}

/// Full session data — meta + state + optional last checkpoint + config snapshot.
///
/// `PartialEq` is intentionally not derived because `arf_core::State` doesn't
/// implement `PartialEq`. Use `serde_json::to_value(&a) == serde_json::to_value(&b)`
/// for value comparison.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionData {
    pub meta: SessionMeta,
    pub state: State,
    pub last_checkpoint: Option<CheckpointSnapshot>,
    pub config_snapshot: serde_json::Value,
}

// ── Error type ───────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum SessionError {
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("serialization error: {0}")]
    Serde(#[from] serde_json::Error),
    #[error("session not found: {0}")]
    NotFound(String),
    #[error("corrupt data: {0}")]
    Corrupt(String),
}

// ── SessionStore trait ───────────────────────────────────────────────

/// The four persistence side effects that a spec-compliant `snapshot()` MUST
/// perform. Returned by [`SessionStore::snapshot`] so callers (and custom impl
/// authors) can see exactly what changed — the trait previously said only
/// "append a checkpoint" while `SqliteSessionStore` silently also rewrote
/// `state_json`, bumped `updated_at`, and forced `status='interrupted'`
/// (Phase 9 F-014).
#[derive(Debug, Clone)]
pub struct SnapshotEffects {
    /// 1. `captured_at` of the checkpoint row that was written (its PK component).
    pub checkpoint_written: DateTime<Utc>,
    /// 2. Whether `sessions.state_json` was refreshed to the latest `state`.
    pub state_updated: bool,
    /// 3. The new `sessions.updated_at` timestamp.
    pub updated_at: DateTime<Utc>,
    /// 4. The status forced on the session — always [`SessionStatus::Interrupted`]
    ///    (the kill-signal marker for replay-on-restart).
    pub status_forced: SessionStatus,
}

/// Abstract session persistence. Implementations: [`SqliteSessionStore`].
/// Engine interacts via this trait, not the concrete type.
#[async_trait]
pub trait SessionStore: Send + Sync {
    /// List all sessions, ordered by `updated_at DESC`.
    async fn list(&self) -> Result<Vec<SessionMeta>, SessionError>;

    /// Load a session's full data (state + last checkpoint + config).
    /// Returns `None` if `session_id` not found.
    async fn load(&self, session_id: &str) -> Result<Option<SessionData>, SessionError>;

    /// Return `true` if a session with `session_id` exists in the store.
    ///
    /// Default impl is `load(...).is_some()`; impls may override with a cheaper
    /// existence probe. Used by the Engine to fail fast when a session was never
    /// pre-saved (Phase 9 F-012).
    async fn exists(&self, session_id: &str) -> Result<bool, SessionError> {
        Ok(self.load(session_id).await?.is_some())
    }

    /// Persist session data — **all four** `SessionData` fields, including
    /// `last_checkpoint`.
    ///
    /// If `data.last_checkpoint` is `Some`, the checkpoint is written to the
    /// checkpoints store as well (so a subsequent `load()` round-trips it); if
    /// `None`, existing checkpoints are left untouched (call this to persist just
    /// meta+state without disturbing checkpoints). If the session_id already
    /// exists, the sessions row is replaced (caller preserves `created_at` via
    /// meta).
    ///
    /// Phase 9 F-013: previously `save()` silently dropped `last_checkpoint`,
    /// forcing callers to also call `snapshot()`.
    async fn save(&self, data: &SessionData) -> Result<(), SessionError>;

    /// Delete a session and its checkpoints.
    async fn delete(&self, session_id: &str) -> Result<(), SessionError>;

    /// Append a checkpoint snapshot and run **four** side effects (a
    /// spec-compliant custom impl MUST perform all of them — Phase 9 F-014):
    ///
    /// 1. write the checkpoint row (keyed by `session_id` + `captured_at`);
    /// 2. UPDATE `sessions.state_json` to the supplied `state` (push to latest);
    /// 3. UPDATE `sessions.updated_at` to now;
    /// 4. force `sessions.status = 'interrupted'` (the replay kill-signal marker).
    ///
    /// Returns [`SnapshotEffects`] describing what changed. (Engine calls this at
    /// each of the 5 Checkpoint positions.)
    async fn snapshot(
        &self,
        session_id: &str,
        state: &State,
        snapshot: &CheckpointSnapshot,
    ) -> Result<SnapshotEffects, SessionError>;
}

// ── SqliteSessionStore ───────────────────────────────────────────────

/// SQLite-backed `SessionStore`. Single writer; the inner `Connection` is
/// protected by a `tokio::Mutex`. The DB schema is auto-created on `new()`.
pub struct SqliteSessionStore {
    path: PathBuf,
    conn: Arc<Mutex<Connection>>,
}

impl SqliteSessionStore {
    /// Open or create a SQLite store at `path`. Creates the schema if missing.
    pub async fn new(path: impl AsRef<Path>) -> Result<Self, SessionError> {
        let path = path.as_ref().to_path_buf();
        let conn = Connection::open(&path)?;
        let store = Self {
            path,
            conn: Arc::new(Mutex::new(conn)),
        };
        store.init_schema().await?;
        Ok(store)
    }

    /// Open an in-memory SQLite store (test-only).
    pub async fn in_memory() -> Result<Self, SessionError> {
        let conn = Connection::open_in_memory()?;
        let store = Self {
            path: PathBuf::from(":memory:"),
            conn: Arc::new(Mutex::new(conn)),
        };
        store.init_schema().await?;
        Ok(store)
    }

    /// Return the on-disk path (None for in-memory).
    pub fn path(&self) -> &Path {
        &self.path
    }

    async fn init_schema(&self) -> Result<(), SessionError> {
        let conn = self.conn.lock().await;
        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                round_count INTEGER NOT NULL DEFAULT 0,
                turn_count  INTEGER NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'active',
                current_round INTEGER,
                state_json  TEXT NOT NULL,
                config_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                session_id   TEXT NOT NULL,
                captured_at  TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (session_id, captured_at),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC);
            "#,
        )?;
        Ok(())
    }
}

#[async_trait]
impl SessionStore for SqliteSessionStore {
    async fn list(&self) -> Result<Vec<SessionMeta>, SessionError> {
        let conn = self.conn.lock().await;
        let mut stmt = conn.prepare(
            "SELECT session_id, title, created_at, updated_at, round_count, turn_count, status, current_round
             FROM sessions ORDER BY updated_at DESC",
        )?;
        let rows = stmt.query_map([], |row| {
            let created: String = row.get(2)?;
            let updated: String = row.get(3)?;
            let status: String = row.get(6)?;
            let created_at = parse_dt(&created)
                .map_err(|e| rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(e)))?;
            let updated_at = parse_dt(&updated)
                .map_err(|e| rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(e)))?;
            let status = parse_status(&status)
                .map_err(|e| rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(e)))?;
            Ok(SessionMeta {
                session_id: row.get(0)?,
                title: row.get(1)?,
                created_at,
                updated_at,
                round_count: row.get::<_, i64>(4)? as usize,
                turn_count: row.get::<_, i64>(5)? as usize,
                status,
                current_round: row.get::<_, Option<i64>>(7)?.map(|n| n as usize),
            })
        })?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    async fn load(&self, session_id: &str) -> Result<Option<SessionData>, SessionError> {
        let conn = self.conn.lock().await;
        let row: Option<(String, String, String, String, i64, i64, String, Option<i64>, String, String)> =
            conn.query_row(
                "SELECT session_id, title, created_at, updated_at, round_count, turn_count, status, current_round, state_json, config_json
                 FROM sessions WHERE session_id = ?1",
                params![session_id],
                |row| {
                    Ok((
                        row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?,
                        row.get(4)?, row.get(5)?, row.get(6)?, row.get(7)?,
                        row.get(8)?, row.get(9)?,
                    ))
                },
            )
            .optional()?;
        let Some((sid, title, created_s, updated_s, rc, tc, status_s, current_round, state_json, config_json)) = row else {
            return Ok(None);
        };

        let checkpoint: Option<CheckpointSnapshot> = conn
            .query_row(
                "SELECT payload_json FROM checkpoints WHERE session_id = ?1 ORDER BY captured_at DESC LIMIT 1",
                params![session_id],
                |row| {
                    let s: String = row.get(0)?;
                    Ok(s)
                },
            )
            .optional()?
            .map(|s| serde_json::from_str(&s))
            .transpose()?;

        let meta = SessionMeta {
            session_id: sid,
            title,
            created_at: parse_dt(&created_s)?,
            updated_at: parse_dt(&updated_s)?,
            round_count: rc as usize,
            turn_count: tc as usize,
            status: parse_status(&status_s)?,
            current_round: current_round.map(|n| n as usize),
        };
        let state: State = serde_json::from_str(&state_json)?;
        let config_snapshot: serde_json::Value = serde_json::from_str(&config_json)?;

        Ok(Some(SessionData {
            meta,
            state,
            last_checkpoint: checkpoint,
            config_snapshot,
        }))
    }

    async fn save(&self, data: &SessionData) -> Result<(), SessionError> {
        let conn = self.conn.lock().await;
        let state_json = serde_json::to_string(&data.state)?;
        let config_json = serde_json::to_string(&data.config_snapshot)?;
        let created = data.meta.created_at.to_rfc3339();
        let updated = data.meta.updated_at.to_rfc3339();
        let current_round = data.meta.current_round.map(|n| n as i64);
        conn.execute(
            "INSERT INTO sessions (session_id, title, created_at, updated_at, round_count, turn_count, status, current_round, state_json, config_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
             ON CONFLICT(session_id) DO UPDATE SET
               title = excluded.title,
               updated_at = excluded.updated_at,
               round_count = excluded.round_count,
               turn_count = excluded.turn_count,
               status = excluded.status,
               current_round = excluded.current_round,
               state_json = excluded.state_json,
               config_json = excluded.config_json",
            params![
                data.meta.session_id,
                data.meta.title,
                created,
                updated,
                data.meta.round_count as i64,
                data.meta.turn_count as i64,
                data.meta.status.as_str(),
                current_round,
                state_json,
                config_json,
            ],
        )?;
        // F-013: persist last_checkpoint too (previously silently dropped). A
        // None checkpoint leaves any existing checkpoints untouched.
        if let Some(snap) = &data.last_checkpoint {
            let payload_json = serde_json::to_string(snap)?;
            let captured_at = snap.captured_at.to_rfc3339();
            conn.execute(
                "INSERT OR REPLACE INTO checkpoints (session_id, captured_at, payload_json) VALUES (?1, ?2, ?3)",
                params![data.meta.session_id, captured_at, payload_json],
            )?;
        }
        Ok(())
    }

    async fn delete(&self, session_id: &str) -> Result<(), SessionError> {
        let conn = self.conn.lock().await;
        conn.execute("DELETE FROM checkpoints WHERE session_id = ?1", params![session_id])?;
        let n = conn.execute("DELETE FROM sessions WHERE session_id = ?1", params![session_id])?;
        if n == 0 {
            return Err(SessionError::NotFound(session_id.into()));
        }
        Ok(())
    }

    async fn snapshot(
        &self,
        session_id: &str,
        state: &State,
        snapshot: &CheckpointSnapshot,
    ) -> Result<SnapshotEffects, SessionError> {
        let conn = self.conn.lock().await;
        // Check existence first to surface NotFound (instead of FK violation).
        let exists: bool = conn
            .query_row(
                "SELECT 1 FROM sessions WHERE session_id = ?1",
                params![session_id],
                |_| Ok(true),
            )
            .optional()?
            .unwrap_or(false);
        if !exists {
            return Err(SessionError::NotFound(session_id.into()));
        }
        // Effect 1: persist checkpoint row.
        let payload_json = serde_json::to_string(snapshot)?;
        let captured_at = snapshot.captured_at.to_rfc3339();
        conn.execute(
            "INSERT OR REPLACE INTO checkpoints (session_id, captured_at, payload_json) VALUES (?1, ?2, ?3)",
            params![session_id, captured_at, payload_json],
        )?;
        // Effects 2-4: push state to latest, bump updated_at, force interrupted.
        let state_json = serde_json::to_string(state)?;
        let updated_at = Utc::now();
        let updated = updated_at.to_rfc3339();
        conn.execute(
            "UPDATE sessions SET state_json = ?1, updated_at = ?2, status = 'interrupted' WHERE session_id = ?3",
            params![state_json, updated, session_id],
        )?;
        Ok(SnapshotEffects {
            checkpoint_written: snapshot.captured_at,
            state_updated: true,
            updated_at,
            status_forced: SessionStatus::Interrupted,
        })
    }
}

fn parse_dt(s: &str) -> Result<DateTime<Utc>, SessionError> {
    DateTime::parse_from_rfc3339(s)
        .map(|dt| dt.with_timezone(&Utc))
        .map_err(|e| SessionError::Corrupt(format!("invalid datetime '{s}': {e}")))
}

fn parse_status(s: &str) -> Result<SessionStatus, SessionError> {
    SessionStatus::from_str(s)
}

// ── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::ModelMessage;

    fn make_meta(id: &str) -> SessionMeta {
        SessionMeta::new(id, "test title")
    }

    fn make_data(id: &str) -> SessionData {
        SessionData {
            meta: make_meta(id),
            state: State::new(),
            last_checkpoint: None,
            config_snapshot: serde_json::json!({"model": "test"}),
        }
    }

    // [构造] SessionMeta::new 默认 Active 状态
    #[test]
    fn session_meta_new_defaults() {
        let m = SessionMeta::new("s1", "title1");
        assert_eq!(m.session_id, "s1");
        assert_eq!(m.title, "title1");
        assert_eq!(m.status, SessionStatus::Active);
        assert_eq!(m.round_count, 0);
    }

    // [trait] SessionStatus 序列化/反序列化 round-trip
    #[test]
    fn session_status_roundtrip() {
        for s in [
            SessionStatus::Active,
            SessionStatus::Completed,
            SessionStatus::Interrupted,
        ] {
            let json = serde_json::to_string(&s).unwrap();
            let back: SessionStatus = serde_json::from_str(&json).unwrap();
            assert_eq!(s, back);
        }
    }

    // [边界] SessionStatus 未知字符串 → Corrupt 错误
    #[test]
    fn session_status_unknown_str_errors() {
        let err = SessionStatus::from_str("invalid").unwrap_err();
        match err {
            SessionError::Corrupt(_) => {}
            other => panic!("expected Corrupt, got {other:?}"),
        }
    }

    // [构造] SqliteSessionStore::in_memory 创建后 schema 已就绪
    #[tokio::test]
    async fn sqlite_in_memory_creates_schema() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        // list should be empty
        let sessions = store.list().await.unwrap();
        assert!(sessions.is_empty());
    }

    // [方法] save + load 完整 round-trip
    #[tokio::test]
    async fn save_load_roundtrip() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let data = make_data("sess-1");
        store.save(&data).await.unwrap();
        let loaded = store.load("sess-1").await.unwrap().unwrap();
        assert_eq!(loaded.meta.session_id, "sess-1");
        assert_eq!(loaded.meta.title, "test title");
        assert_eq!(loaded.state.messages.len(), 0);
        assert_eq!(loaded.config_snapshot, serde_json::json!({"model": "test"}));
    }

    // [方法] load 不存在的 session 返回 None
    #[tokio::test]
    async fn load_nonexistent_returns_none() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let loaded = store.load("nope").await.unwrap();
        assert!(loaded.is_none());
    }

    // [方法] list 多个 session 按 updated_at DESC 排序
    #[tokio::test]
    async fn list_orders_by_updated_desc() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let mut d1 = make_data("s1");
        d1.meta.updated_at = Utc::now() - chrono::Duration::seconds(10);
        let mut d2 = make_data("s2");
        d2.meta.updated_at = Utc::now();
        let mut d3 = make_data("s3");
        d3.meta.updated_at = Utc::now() - chrono::Duration::seconds(5);
        store.save(&d1).await.unwrap();
        store.save(&d2).await.unwrap();
        store.save(&d3).await.unwrap();
        let list = store.list().await.unwrap();
        assert_eq!(list.len(), 3);
        assert_eq!(list[0].session_id, "s2");
        assert_eq!(list[1].session_id, "s3");
        assert_eq!(list[2].session_id, "s1");
    }

    // [方法] delete 删除后 load 返回 None
    #[tokio::test]
    async fn delete_removes_session() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let data = make_data("sess-1");
        store.save(&data).await.unwrap();
        store.delete("sess-1").await.unwrap();
        let loaded = store.load("sess-1").await.unwrap();
        assert!(loaded.is_none());
    }

    // [边界] delete 不存在的 session → NotFound 错误
    #[tokio::test]
    async fn delete_nonexistent_errors() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let err = store.delete("nope").await.unwrap_err();
        match err {
            SessionError::NotFound(_) => {}
            other => panic!("expected NotFound, got {other:?}"),
        }
    }

    // [方法] snapshot 写入后 load 能读出
    #[tokio::test]
    async fn snapshot_then_load_returns_checkpoint() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let mut data = make_data("sess-1");
        // Add a message so state isn't empty
        data.state.push_message(ModelMessage::new("user", "hi"));
        store.save(&data).await.unwrap();

        let cp = CheckpointSnapshot::new(Checkpoint::AfterModelCall, 5);
        store.snapshot("sess-1", &data.state, &cp).await.unwrap();

        let loaded = store.load("sess-1").await.unwrap().unwrap();
        assert!(loaded.last_checkpoint.is_some());
        let cp_back = loaded.last_checkpoint.unwrap();
        assert_eq!(cp_back.checkpoint, Checkpoint::AfterModelCall);
        assert_eq!(cp_back.turn_index, 5);
        // After snapshot, status should be 'interrupted'
        assert_eq!(loaded.meta.status, SessionStatus::Interrupted);
    }

    // [方法] snapshot 不存在的 session → NotFound
    #[tokio::test]
    async fn snapshot_nonexistent_errors() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let cp = CheckpointSnapshot::new(Checkpoint::BeforeModelCall, 0);
        let err = store
            .snapshot("nope", &State::new(), &cp)
            .await
            .unwrap_err();
        match err {
            SessionError::NotFound(_) => {}
            other => panic!("expected NotFound, got {other:?}"),
        }
    }

    // [方法] save() 持久化 last_checkpoint（F-013）
    #[tokio::test]
    async fn save_persists_last_checkpoint() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let mut data = make_data("sess-1");
        let cp = CheckpointSnapshot::new(Checkpoint::AfterModelCall, 3);
        data.last_checkpoint = Some(cp);
        store.save(&data).await.unwrap();

        // Reload — the checkpoint must round-trip (was silently dropped before).
        let loaded = store.load("sess-1").await.unwrap().unwrap();
        let cp_back = loaded
            .last_checkpoint
            .expect("save() must persist last_checkpoint");
        assert_eq!(cp_back.checkpoint, Checkpoint::AfterModelCall);
        assert_eq!(cp_back.turn_index, 3);
    }

    // [方法] snapshot() 返回 4 个副作用（F-014）
    #[tokio::test]
    async fn snapshot_returns_4_effects() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let data = make_data("sess-1");
        store.save(&data).await.unwrap();

        let cp = CheckpointSnapshot::new(Checkpoint::AfterModelCall, 2);
        let effects = store.snapshot("sess-1", &data.state, &cp).await.unwrap();

        // 1. checkpoint row written (captured_at matches)
        assert_eq!(effects.checkpoint_written, cp.captured_at);
        // 2. state pushed to latest
        assert!(effects.state_updated);
        // 3. updated_at bumped (>= checkpoint capture time)
        assert!(effects.updated_at >= cp.captured_at);
        // 4. status forced to interrupted
        assert_eq!(effects.status_forced, SessionStatus::Interrupted);
    }

    // [方法] save 覆盖已存在 session（更新字段）
    #[tokio::test]
    async fn save_overwrites_existing() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let mut data = make_data("sess-1");
        store.save(&data).await.unwrap();

        // Modify and re-save
        data.meta.title = "new title".into();
        data.meta.status = SessionStatus::Completed;
        data.state.push_message(ModelMessage::new("user", "x"));
        store.save(&data).await.unwrap();

        let loaded = store.load("sess-1").await.unwrap().unwrap();
        assert_eq!(loaded.meta.title, "new title");
        assert_eq!(loaded.meta.status, SessionStatus::Completed);
        assert_eq!(loaded.state.messages.len(), 1);
    }

    // [序列化] SessionData 完整序列化/反序列化（含 state + checkpoint）
    #[tokio::test]
    async fn session_data_serde_with_checkpoint() {
        let mut data = make_data("sess-1");
        data.state.push_message(ModelMessage::new("user", "hello"));
        data.state.push_message(ModelMessage::new("assistant", "hi back"));
        data.last_checkpoint = Some(CheckpointSnapshot {
            checkpoint: Checkpoint::BeforeToolExec,
            turn_index: 7,
            pending_messages: vec![ModelMessage::new("tool", "result")],
            wait_events: vec![],
            captured_at: Utc::now(),
            tasks_json: serde_json::Value::Null,
        });
        let json = serde_json::to_string(&data).unwrap();
        let back: SessionData = serde_json::from_str(&json).unwrap();
        assert_eq!(back.state.messages.len(), 2);
        assert!(back.last_checkpoint.is_some());
        assert_eq!(
            back.last_checkpoint.unwrap().checkpoint,
            Checkpoint::BeforeToolExec
        );
    }

    // [边界] 包含 NodeId 的 state 序列化往返（State 内部已测，这里冒烟）
    #[tokio::test]
    async fn state_with_complex_data_roundtrips() {
        let store = SqliteSessionStore::in_memory().await.unwrap();
        let mut data = make_data("sess-1");
        data.state.push_message(
            ModelMessage::new("assistant", "r")
                .with_tool_call_id("call_0")
                .with_name("read_file"),
        );
        store.save(&data).await.unwrap();
        let loaded = store.load("sess-1").await.unwrap().unwrap();
        assert_eq!(loaded.state.messages[0].tool_call_id, Some("call_0".into()));
        assert_eq!(loaded.state.messages[0].name, Some("read_file".into()));
    }

    // [方法] CheckpointSnapshot::new 初始化默认值
    #[test]
    fn checkpoint_snapshot_new_defaults() {
        let cp = CheckpointSnapshot::new(Checkpoint::RoundEnd, 0);
        assert_eq!(cp.checkpoint, Checkpoint::RoundEnd);
        assert_eq!(cp.turn_index, 0);
        assert!(cp.pending_messages.is_empty());
        assert!(cp.wait_events.is_empty());
        assert!(cp.tasks_json.is_null());
    }
}
