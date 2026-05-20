"""SQLite store for trace observability, sessions, and usage tracking."""

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    username      TEXT NOT NULL DEFAULT 'admin',
    title         TEXT DEFAULT '新会话',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    filepath      TEXT,
    turn_count    INTEGER DEFAULT 0,
    json_size_mb  REAL DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    hidden        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_username_updated ON sessions(username, updated_at DESC);

CREATE TABLE IF NOT EXISTS usage_records (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    username          TEXT NOT NULL DEFAULT 'admin',
    model_name        TEXT NOT NULL,
    model_type        TEXT NOT NULL DEFAULT 'deep_thinking',
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_user_created ON usage_records(username, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_records(model_name);

CREATE TABLE IF NOT EXISTS model_pricing (
    username     TEXT NOT NULL DEFAULT 'admin',
    model_name   TEXT NOT NULL,
    input_price  REAL NOT NULL DEFAULT 0,
    output_price REAL NOT NULL DEFAULT 0,
    currency     TEXT NOT NULL DEFAULT 'CNY',
    updated_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (username, model_name)
);

CREATE TABLE IF NOT EXISTS trace_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    username      TEXT NOT NULL DEFAULT 'admin',
    turn          INTEGER NOT NULL,
    node          TEXT NOT NULL,
    event_type    TEXT NOT NULL DEFAULT '',
    model         TEXT,
    tool_name     TEXT,
    duration_ms   REAL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens  INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'ok',
    error_msg     TEXT,
    metadata      TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trace_events_session ON trace_events(session_id, turn);

CREATE TABLE IF NOT EXISTS message_feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    rating        INTEGER NOT NULL,
    feedback_text TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_feedback_session ON message_feedback(session_id);

CREATE TABLE IF NOT EXISTS session_cost (
    session_id       TEXT PRIMARY KEY,
    total_prompt_tokens  INTEGER DEFAULT 0,
    total_completion_tokens INTEGER DEFAULT 0,
    total_tokens     INTEGER DEFAULT 0,
    estimated_cost   REAL DEFAULT 0.0,
    currency         TEXT DEFAULT 'CNY',
    model_breakdown  TEXT,
    updated_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prompts (
    prompt_hash   TEXT PRIMARY KEY,
    prompt_full   TEXT NOT NULL,
    prompt_length INTEGER NOT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
);
"""

_EVENT_TYPE_BY_NODE = {
    "call_model": "graph.call_model",
    "execute_tools": "graph.execute_tools",
    "hook": "graph.hook",
    "classify": "graph.classify",
    "respond": "graph.respond",
    "recovery": "graph.recovery",
    "compact": "lifecycle.compaction",
}

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()
_db_path: str = ""


def _get_conn(db_path: str = "") -> sqlite3.Connection:
    global _conn
    if _conn is None:
        p = db_path or _db_path
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(p, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()
        _migrate_schema()
    return _conn


def _migrate_schema():
    """Apply schema migrations for columns added after initial release."""
    conn = _get_conn()

    # event_type on trace_events (pre-0.1.0)
    cur = conn.execute("PRAGMA table_info(trace_events)")
    cols = [r["name"] for r in cur.fetchall()]
    if "event_type" not in cols:
        conn.execute("ALTER TABLE trace_events ADD COLUMN event_type TEXT NOT NULL DEFAULT ''")
        for node, etype in _EVENT_TYPE_BY_NODE.items():
            conn.execute("UPDATE trace_events SET event_type = ? WHERE node = ?", (etype, node))

    # created_at on trace_events
    if "created_at" not in cols:
        conn.execute("ALTER TABLE trace_events ADD COLUMN created_at TEXT DEFAULT (datetime('now'))")

    # hidden on sessions (soft-delete)
    cur = conn.execute("PRAGMA table_info(sessions)")
    sess_cols = [r["name"] for r in cur.fetchall()]
    if "hidden" not in sess_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN hidden INTEGER DEFAULT 0")

    conn.commit()


def init_db(db_path: str) -> None:
    global _db_path
    _db_path = db_path
    _get_conn(db_path)
    _migrate_schema()


# ---- trace events --------------------------------------------------------


def _normalize_trace_event(e: dict) -> dict:
    e = dict(e)
    if not e.get("event_type") and e.get("node") in _EVENT_TYPE_BY_NODE:
        e["event_type"] = _EVENT_TYPE_BY_NODE[e["node"]]
    # Coerce nullable fields to safe defaults (node has NOT NULL constraint)
    if e.get("node") is None:
        e["node"] = ""
    if "tool" in e and not e.get("tool_name"):
        e["tool_name"] = e["tool"]
    if e.get("ok") is True and not e.get("status"):
        e["status"] = "ok"
    elif e.get("error") is True and not e.get("status"):
        e["status"] = "error"
    elif e.get("blocked_by_hook") and not e.get("status"):
        e["status"] = "blocked_by_hook"
    if not e.get("status"):
        e["status"] = "ok"
    meta = e.get("metadata")
    if isinstance(meta, dict):
        import json as _json
        e["metadata"] = _json.dumps(meta, ensure_ascii=False)
    return e


def insert_trace_events(events: list[dict], workspace_dir: str = "") -> None:
    if not events:
        return
    with _lock:
        conn = _get_conn()
        rows = []
        session_id = ""
        for e in events:
            ne = _normalize_trace_event(e)
            session_id = ne.get("session_id", "")
            rows.append((
                session_id,
                ne.get("username", "admin"),
                ne.get("turn", 0),
                ne.get("node", ""),
                ne.get("event_type", ""),
                ne.get("model"),
                ne.get("tool_name"),
                ne.get("duration_ms"),
                ne.get("prompt_tokens", 0),
                ne.get("completion_tokens", 0),
                ne.get("total_tokens", 0),
                ne.get("status", "ok"),
                ne.get("error_msg"),
                ne.get("metadata"),
                ne.get("timestamp", ne.get("created_at", _now())),
            ))
        conn.executemany(
            """INSERT INTO trace_events
               (session_id, username, turn, node, event_type, model, tool_name,
                duration_ms, prompt_tokens, completion_tokens, total_tokens,
                status, error_msg, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()


def get_trace_session_list(username: str = "admin", limit: int = 20) -> list[dict]:
    with _lock:
        cur = _get_conn().execute(
            """SELECT session_id, username, MIN(created_at) as started_at, MAX(created_at) as ended_at,
                      COUNT(*) as event_count, SUM(total_tokens) as total_tokens,
                      SUM(duration_ms) as total_duration_ms
               FROM trace_events
               WHERE username = ?
               GROUP BY session_id
               ORDER BY started_at DESC
               LIMIT ?""",
            (username, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_trace_session_detail(session_id: str, username: str = "admin") -> list[dict]:
    with _lock:
        cur = _get_conn().execute(
            """SELECT * FROM trace_events
               WHERE session_id = ? AND username = ?
               ORDER BY turn, id""",
            (session_id, username),
        )
        return [dict(r) for r in cur.fetchall()]


def get_trace_summary(username: str = "admin") -> dict:
    with _lock:
        cur = _get_conn().execute(
            """SELECT COUNT(*) as total_events,
                      COUNT(DISTINCT session_id) as total_sessions,
                      SUM(total_tokens) as total_tokens,
                      SUM(duration_ms) as total_duration_ms
               FROM trace_events WHERE username = ?""",
            (username,),
        )
        row = dict(cur.fetchone())
        return {k: (v or 0) for k, v in row.items()}


# ---- resource statistics -------------------------------------------------


def get_resource_stats(username: str = "admin", period: str = "all") -> list[dict]:
    date_filter = {
        "today": "AND te.created_at >= datetime('now', 'start of day')",
        "week": "AND te.created_at >= datetime('now', '-7 days')",
        "month": "AND te.created_at >= datetime('now', 'start of month')",
        "all": "",
    }.get(period, "")

    with _lock:
        rows = _get_conn().execute(
            f"""SELECT te.tool_name as name,
                       COUNT(*) as call_count,
                       SUM(CASE WHEN te.status = 'ok' THEN 1 ELSE 0 END) as success_count,
                       SUM(CASE WHEN te.status = 'error' THEN 1 ELSE 0 END) as failure_count,
                       SUM(CASE WHEN te.status = 'ok' THEN te.duration_ms ELSE 0 END) as success_duration_sum
                FROM trace_events te
                WHERE te.username = ?
                  AND te.event_type = 'graph.execute_tools'
                  AND te.tool_name IS NOT NULL
                  {date_filter}
                GROUP BY te.tool_name
                ORDER BY call_count DESC
            """,
            (username,),
        ).fetchall()

    result = []
    for r in rows:
        sc = r["success_count"] or 0
        avg_dur = (r["success_duration_sum"] / sc) if sc > 0 else 0
        result.append({
            "name": r["name"],
            "call_count": r["call_count"],
            "success_count": sc,
            "failure_count": r["failure_count"] or 0,
            "avg_duration_ms": round(avg_dur, 1),
        })
    return result


def get_resource_detail(
    username: str = "admin", resource_name: str = "", from_date: str = "", to_date: str = ""
) -> list[dict]:
    where_extra = ""
    params: list = [username, resource_name]
    if from_date:
        where_extra += " AND te.created_at >= ?"
        params.append(from_date)
    if to_date:
        where_extra += " AND te.created_at <= ?"
        params.append(to_date + " 23:59:59")

    with _lock:
        rows = _get_conn().execute(
            f"""SELECT date(te.created_at) as day,
                       COUNT(*) as call_count,
                       SUM(CASE WHEN te.status = 'ok' THEN 1 ELSE 0 END) as success_count,
                       SUM(CASE WHEN te.status = 'error' THEN 1 ELSE 0 END) as failure_count,
                       AVG(CASE WHEN te.status = 'ok' THEN te.duration_ms ELSE NULL END) as avg_duration_ms
                FROM trace_events te
                WHERE te.username = ?
                  AND te.tool_name = ?
                  AND te.event_type = 'graph.execute_tools'
                  {where_extra}
                GROUP BY day
                ORDER BY day ASC
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# ---- message feedback ----------------------------------------------------


def insert_feedback(session_id: str, message_index: int, rating: int, feedback_text: str = "") -> None:
    with _lock:
        _get_conn().execute(
            "INSERT INTO message_feedback (session_id, message_index, rating, feedback_text) VALUES (?, ?, ?, ?)",
            (session_id, message_index, rating, feedback_text or None),
        )
        _get_conn().commit()


def get_feedback_for_session(session_id: str) -> list[dict]:
    with _lock:
        cur = _get_conn().execute(
            "SELECT * FROM message_feedback WHERE session_id = ? ORDER BY message_index",
            (session_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_feedback_summary(username: str = "admin") -> dict:
    with _lock:
        cur = _get_conn().execute(
            """SELECT SUM(CASE WHEN f.rating = 1 THEN 1 ELSE 0 END) as thumbs_up,
                      SUM(CASE WHEN f.rating = -1 THEN 1 ELSE 0 END) as thumbs_down,
                      COUNT(*) as total
               FROM message_feedback f
               JOIN trace_events t ON f.session_id = t.session_id
               WHERE t.username = ?""",
            (username,),
        )
        row = cur.fetchone()
        if row:
            u = row["thumbs_up"] or 0
            d = row["thumbs_down"] or 0
            return {"thumbs_up": u, "thumbs_down": d, "total": u + d}
        return {"thumbs_up": 0, "thumbs_down": 0, "total": 0}


# ---- session cost --------------------------------------------------------


def save_session_cost(session_id: str, total_tokens: int, prompt_tokens: int, completion_tokens: int, model_breakdown: dict | None = None, estimated_cost: float = 0.0) -> None:
    import json as _json
    with _lock:
        _get_conn().execute(
            """INSERT OR REPLACE INTO session_cost
               (session_id, total_tokens, total_prompt_tokens, total_completion_tokens, estimated_cost, model_breakdown, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (session_id, total_tokens, prompt_tokens, completion_tokens, estimated_cost, _json.dumps(model_breakdown or {}, ensure_ascii=False)),
        )
        _get_conn().commit()


def get_session_cost(session_id: str) -> dict | None:
    with _lock:
        cur = _get_conn().execute(
            "SELECT * FROM session_cost WHERE session_id = ?", (session_id,)
        )
        row = cur.fetchone()
        if row:
            d = dict(row)
            import json as _json
            if d.get("model_breakdown") and isinstance(d["model_breakdown"], str):
                d["model_breakdown"] = _json.loads(d["model_breakdown"])
            return d
        return None


# ---- usage records ------------------------------------------------------


def record_usage(username: str = "admin", model_name: str = "", model_type: str = "",
                 prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    total = prompt_tokens + completion_tokens
    with _lock:
        _get_conn().execute(
            "INSERT INTO usage_records (username, model_name, model_type, "
            "prompt_tokens, completion_tokens, total_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, model_name, model_type, prompt_tokens, completion_tokens, total),
        )
        _get_conn().commit()


def get_usage_summary(username: str = "admin", period: str = "month") -> dict:
    date_filter = {
        "today": "date(created_at) = date('now')",
        "week": "created_at >= datetime('now', '-7 days')",
        "month": "created_at >= datetime('now', 'start of month')",
    }.get(period, "created_at >= datetime('now', 'start of month')")

    with _lock:
        rows = _get_conn().execute(
            f"SELECT model_name, model_type, COUNT(*) as calls, "
            f"SUM(prompt_tokens) as pt, SUM(completion_tokens) as ct, "
            f"SUM(total_tokens) as tt FROM usage_records "
            f"WHERE username = ? AND {date_filter} "
            f"GROUP BY model_name ORDER BY tt DESC",
            (username,),
        ).fetchall()

    by_model = []
    total_tokens = 0
    total_calls = 0
    for r in rows:
        total_tokens += r["tt"]
        total_calls += r["calls"]
        by_model.append({
            "model_name": r["model_name"],
            "model_type": r["model_type"],
            "prompt_tokens": r["pt"],
            "completion_tokens": r["ct"],
            "total_tokens": r["tt"],
            "calls": r["calls"],
        })
    return {
        "total_tokens": total_tokens,
        "total_calls": total_calls,
        "by_model": by_model,
    }


def get_usage_detail(username: str = "admin", from_date: str = "", to_date: str = "",
                     model_name: str | None = None) -> list[dict]:
    with _lock:
        query = ("SELECT date(created_at) as day, model_name, "
                 "SUM(prompt_tokens) as pt, SUM(completion_tokens) as ct, "
                 "COUNT(*) as calls FROM usage_records "
                 "WHERE username = ? AND created_at >= ? AND created_at <= ? ")
        params: list = [username, from_date, to_date + " 23:59:59"]
        if model_name:
            query += "AND model_name = ? "
            params.append(model_name)
        query += "GROUP BY day, model_name ORDER BY day DESC"
        rows = _get_conn().execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_model_pricing(username: str = "admin") -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            "SELECT model_name, input_price, output_price, currency "
            "FROM model_pricing WHERE username = ?",
            (username,),
        ).fetchall()
        return [dict(r) for r in rows]


def set_model_pricing(username: str = "admin", model_name: str = "",
                      input_price: float = 0, output_price: float = 0,
                      currency: str = "CNY") -> None:
    with _lock:
        _get_conn().execute(
            "INSERT OR REPLACE INTO model_pricing (username, model_name, "
            "input_price, output_price, currency, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (username, model_name, input_price, output_price, currency),
        )
        _get_conn().commit()


# ---- sessions -----------------------------------------------------------


def insert_session(session_id: str, username: str = "admin", title: str = "", filepath: str | None = None) -> None:
    now = _now()
    with _lock:
        _get_conn().execute(
            "INSERT OR REPLACE INTO sessions (session_id, username, title, created_at, updated_at, filepath) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, username, title, now, now, filepath),
        )
        _get_conn().commit()


def update_session(session_id: str, **kwargs) -> None:
    allowed = {"title", "filepath", "turn_count", "json_size_mb", "message_count"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [session_id]
    with _lock:
        _get_conn().execute(
            f"UPDATE sessions SET {set_clause} WHERE session_id = ?",
            values,
        )
        _get_conn().commit()


def list_sessions(username: str = "admin") -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            "SELECT session_id AS id, title, created_at, updated_at, filepath, "
            "turn_count, json_size_mb, message_count "
            "FROM sessions WHERE username = ? AND hidden = 0 ORDER BY updated_at DESC",
            (username,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_session(session_id: str) -> dict | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_session_db(session_id: str) -> bool:
    """Soft-delete a session: set hidden=1, preserving the record and any associated traces."""
    with _lock:
        cur = _get_conn().execute(
            "UPDATE sessions SET hidden = 1 WHERE session_id = ? AND hidden = 0",
            (session_id,),
        )
        _get_conn().commit()
        return cur.rowcount > 0


# ---- prompts -------------------------------------------------------------


def insert_prompt(prompt_hash: str, prompt_full: str) -> None:
    with _lock:
        _get_conn().execute(
            "INSERT OR IGNORE INTO prompts (prompt_hash, prompt_full, prompt_length) VALUES (?, ?, ?)",
            (prompt_hash, prompt_full, len(prompt_full)),
        )
        _get_conn().commit()


def get_prompt(prompt_hash: str) -> str | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT prompt_full FROM prompts WHERE prompt_hash = ?", (prompt_hash,)
        ).fetchone()
        return row["prompt_full"] if row else None


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
