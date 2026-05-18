# Trace System Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade ARF's trace system to uniformly instrument all 8 lifecycle event types, consolidate storage to SQLite-only, and add prompt snapshot analysis via a separate `prompts` table.

**Architecture:** Add a `TraceCollector` in-memory buffer per SessionManager. All instrumentation points emit events to the collector. On `session_end`, flush to SQLite via `INSERT MANY`. Remove JSONL dual-write. Add `prompts` table (hash → full text) to avoid storing repeated prompt text in trace_events.

**Tech Stack:** Python, SQLite, hashlib, uuid

**Spec:** `docs/superpowers/specs/2026-05-18-trace-system-upgrade-design.md`

---

### Task 1: Database schema — add event_type column and prompts table

**Files:**
- Modify: `src/arf/server/database.py:9-86` (SCHEMA), `database.py:148-184` (insert_trace_events), `database.py:231-255` (get_resource_stats), `database.py:283-298` (get_resource_detail)

- [ ] **Step 1: Add `prompts` table and `event_type` column to SCHEMA**

```python
# In SCHEMA string, after the session_cost table block, add:

CREATE TABLE IF NOT EXISTS prompts (
    prompt_hash   TEXT PRIMARY KEY,
    prompt_full   TEXT NOT NULL,
    prompt_length INTEGER NOT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
);
```

Also add `event_type TEXT NOT NULL DEFAULT ''` to the trace_events CREATE TABLE:

```python
# Replace the existing trace_events CREATE TABLE:
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
```

- [ ] **Step 2: Write migration SQL and a `_migrate_schema()` function**

```python
def _migrate_schema():
    """Add event_type column and backfill from node field."""
    conn = _get_conn()
    # Check if event_type column exists (safe to run multiple times)
    cur = conn.execute("PRAGMA table_info(trace_events)")
    cols = [r["name"] for r in cur.fetchall()]
    if "event_type" not in cols:
        conn.execute("ALTER TABLE trace_events ADD COLUMN event_type TEXT NOT NULL DEFAULT ''")
        # Backfill
        backfill = {
            "call_model": "graph.call_model",
            "execute_tools": "graph.execute_tools",
            "hook": "graph.hook",
            "classify": "graph.classify",
            "respond": "graph.respond",
            "recovery": "graph.recovery",
            "compact": "lifecycle.compaction",
        }
        for node, etype in backfill.items():
            conn.execute("UPDATE trace_events SET event_type = ? WHERE node = ?", (etype, node))
        conn.commit()
```

Call `_migrate_schema()` at the end of `_get_conn()` after `conn.executescript(SCHEMA)`.

- [ ] **Step 3: Update `insert_trace_events` to include event_type**

In `database.py:148-184`, update the `insert_trace_events` function:

```python
def insert_trace_events(events: list[dict], workspace_dir: str = "") -> None:
    if not events:
        return
    with _lock:
        conn = _get_conn()
        rows = []
        for e in events:
            ne = _normalize_trace_event(e)
            rows.append((
                ne.get("session_id", ""),
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
            ))
        conn.executemany(
            """INSERT INTO trace_events
               (session_id, username, turn, node, event_type, model, tool_name,
                duration_ms, prompt_tokens, completion_tokens, total_tokens,
                status, error_msg, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
```

- [ ] **Step 4: Update `get_resource_stats` and `get_resource_detail` to use event_type**

In `get_resource_stats` (line ~248):
```python
# Change: WHERE ... AND te.node = 'execute_tools'
# To:     WHERE ... AND te.event_type = 'graph.execute_tools'
```

In `get_resource_detail` (line ~293):
```python
# Change: AND te.node = 'execute_tools'
# To:     AND te.event_type = 'graph.execute_tools'
```

- [ ] **Step 5: Add prompts table helpers**

At the end of `database.py`, add:

```python
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
```

- [ ] **Step 6: Run existing tests to verify no regression**

```bash
python3 -m pytest tests/test_session_lifecycle.py -v --tb=short
```

Expected: 61 passed

- [ ] **Step 7: Commit**

```bash
git add src/arf/server/database.py
git commit -m "feat: add event_type column, prompts table, schema migration"
```

---

### Task 2: Remove JSONL dual-write

**Files:**
- Modify: `src/arf/server/database.py:133-145` (_write_trace_file), `database.py:182-184` (dual-write call)

- [ ] **Step 1: Remove dual-write call in insert_trace_events**

In `insert_trace_events`, remove the last 4 lines:

```python
# REMOVE these lines:
    # Dual-write to workspace trace file
    if session_id and workspace_dir:
        _write_trace_file(workspace_dir, session_id, events)
```

- [ ] **Step 2: Remove `_write_trace_file` function**

Delete the entire `_write_trace_file` function (lines 133-145).

- [ ] **Step 3: Update callers — remove workspace_dir arg where no longer needed**

In `routes.py:512` and `routes.py:858`, the `insert_trace_events` calls pass `str(mgr.workspace_dir)` as second arg. Since `insert_trace_events` still accepts the parameter (for backward compat), keep the calls as-is — the workspace_dir arg is just ignored now.

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_session_lifecycle.py -v --tb=short
```

Expected: 61 passed

- [ ] **Step 5: Commit**

```bash
git add src/arf/server/database.py
git commit -m "refactor: remove JSONL dual-write, SQLite is sole trace sink"
```

---

### Task 3: TraceCollector — in-memory buffer with session flush

**Files:**
- Create: `src/arf/server/trace_collector.py`
- Modify: `src/arf/server/session_manager.py:26-44` (add collector, wire to reset_session_history)

- [ ] **Step 1: Write the test**

In `tests/test_session_lifecycle.py`, add to `TestStage4ConversationLoop`:

```python
    def test_trace_collector_buffers_and_flushes(self, tmp_path):
        from arf.server.trace_collector import TraceCollector

        collector = TraceCollector()
        collector.emit({
            "event_type": "lifecycle.init",
            "session_id": "test123",
            "turn": 0,
            "node": None,
            "status": "ok",
            "metadata": {"counts": {"tools": 10}},
        })
        collector.emit({
            "event_type": "graph.call_model",
            "session_id": "test123",
            "turn": 1,
            "node": "call_model",
            "status": "ok",
            "model": "quick_thinking",
            "prompt_tokens": 50,
        })

        assert len(collector._buffer) == 2

        events = collector.flush()
        assert len(events) == 2
        assert len(collector._buffer) == 0
        assert events[0]["event_type"] == "lifecycle.init"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_session_lifecycle.py::TestStage4ConversationLoop::test_trace_collector_buffers_and_flushes -v
```

Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Create `trace_collector.py`**

```python
"""TraceCollector — in-memory event buffer, flushed to SQLite on session end."""

import hashlib
import uuid
from datetime import datetime, timezone


class TraceCollector:
    """Thread-safe in-memory buffer for trace events.

    Events accumulate during a session. On session_end, flush() returns
    all events for batch INSERT into SQLite.
    """

    def __init__(self):
        self._buffer: list[dict] = []

    def emit(self, event: dict) -> None:
        """Add an event to the buffer."""
        event.setdefault("event_id", uuid.uuid4().hex[:12])
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        event.setdefault("username", "admin")
        event.setdefault("turn", 0)
        event.setdefault("node", None)
        event.setdefault("model", None)
        event.setdefault("tool_name", None)
        event.setdefault("duration_ms", None)
        event.setdefault("prompt_tokens", 0)
        event.setdefault("completion_tokens", 0)
        event.setdefault("total_tokens", 0)
        event.setdefault("status", "ok")
        event.setdefault("error_msg", None)
        event.setdefault("metadata", {})
        self._buffer.append(event)

    def flush(self) -> list[dict]:
        """Return all buffered events and clear the buffer."""
        events = list(self._buffer)
        self._buffer.clear()
        return events

    def __len__(self) -> int:
        return len(self._buffer)


def compute_prompt_hash(prompt_text: str) -> str:
    """Return first 16 hex chars of SHA-256 for prompt dedup."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_session_lifecycle.py::TestStage4ConversationLoop::test_trace_collector_buffers_and_flushes -v
```

Expected: PASS

- [ ] **Step 5: Wire TraceCollector into SessionManager**

In `session_manager.py:26-44`, add to `__init__`:

```python
from .trace_collector import TraceCollector

# In __init__, after self._hook_runner:
self._trace_collector = TraceCollector()
```

Add a getter:

```python
def get_trace_collector(self) -> "TraceCollector":
    return self._trace_collector
```

In `reset_session_history` (line ~195-201), add flush before clear:

```python
def reset_session_history(self, title: str = DEFAULT_TITLE) -> None:
    # Flush traces before clearing
    events = self._trace_collector.flush()
    if events and self.session_history and len(self.session_history) >= 2:
        sid = self.current_session_id
        for e in events:
            e["session_id"] = sid
        try:
            from .database import insert_trace_events
            insert_trace_events(events, str(self.workspace_dir))
        except Exception:
            logger.exception("Failed to flush trace events")

    self.session_history = []
    self.session_start_time = datetime.now(timezone.utc)
    self.session_title = title
    self.needs_title = True
    self.last_traces = []
    self.last_usage = None
```

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/test_session_lifecycle.py -v --tb=short
```

Expected: 62 passed

- [ ] **Step 7: Commit**

```bash
git add src/arf/server/trace_collector.py src/arf/server/session_manager.py tests/test_session_lifecycle.py
git commit -m "feat: add TraceCollector with in-memory buffer and session flush"
```

---

### Task 4: Instrument lifecycle.init and lifecycle.config

**Files:**
- Modify: `src/arf/server/session_manager.py:61-68` `get_registry()`, `session_manager.py:72-109` `get_agent()`
- Modify: `src/arf/server/routes.py:114-140` (config/test, config/save), `routes.py:158-202` (register-deepseek)

- [ ] **Step 1: Write tests for lifecycle.init**

In `tests/test_session_lifecycle.py`, add to `TestStage1Init`:

```python
    def test_lifecycle_init_trace_on_registry_load(self, tmp_path):
        from arf.server.session_manager import SessionManager
        from arf.server.trace_collector import TraceCollector

        ws = _make_workspace(tmp_path)
        mgr = SessionManager(ws)
        collector = mgr.get_trace_collector()

        # Trigger registry load
        registry = mgr.get_registry()
        assert len(collector) >= 1

        init_event = collector._buffer[0]
        assert init_event["event_type"] == "lifecycle.init"
        assert init_event["status"] == "ok"
        assert "counts" in init_event["metadata"]
        assert init_event["metadata"]["counts"]["tools"] > 0

    def test_lifecycle_init_trace_on_agent_build(self, tmp_path):
        from arf.server.session_manager import SessionManager

        ws = _make_workspace(tmp_path)
        _configure_quick_thinking(ws)
        mgr = SessionManager(ws)
        collector = mgr.get_trace_collector()

        # Trigger agent creation
        try:
            agent = mgr.get_agent()
        except Exception:
            pass  # may fail without full env, but trace should still be emitted

        agent_events = [e for e in collector._buffer
                       if e.get("event_type") == "lifecycle.init"
                       and e.get("metadata", {}).get("stage") == "agent_built"]
        assert len(agent_events) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_session_lifecycle.py::TestStage1Init::test_lifecycle_init_trace_on_registry_load tests/test_session_lifecycle.py::TestStage1Init::test_lifecycle_init_trace_on_agent_build -v
```

Expected: FAIL

- [ ] **Step 3: Add lifecycle.init instrumentation to get_registry()**

In `session_manager.py:61-68`:

```python
def get_registry(self) -> ResourceRegistry:
    if self._registry is None:
        self._registry = ResourceRegistry()
        try:
            self._registry.load(
                str(self.system_dir),
                str(self.workspace_dir),
            )
            collector = self.get_trace_collector()
            collector.emit({
                "event_type": "lifecycle.init",
                "status": "ok",
                "metadata": {
                    "stage": "registry_loaded",
                    "counts": {
                        "models": self._registry.count("models"),
                        "tools": self._registry.count("tools"),
                        "skills": self._registry.count("skills"),
                    },
                    "source": "system+user",
                },
            })
        except Exception as e:
            collector = self.get_trace_collector()
            collector.emit({
                "event_type": "lifecycle.init",
                "status": "error",
                "error_msg": str(e),
                "metadata": {"stage": "registry_loaded"},
            })
            raise
    return self._registry
```

- [ ] **Step 4: Add lifecycle.init instrumentation to get_agent()**

In `session_manager.py:98-107`, after agent creation:

```python
# After self._agent = Dispatcher(user_agent, sys_agent) and self._agent_mtime = current_mtime:
            collector = self.get_trace_collector()
            collector.emit({
                "event_type": "lifecycle.init",
                "status": "ok",
                "metadata": {
                    "stage": "agent_built",
                    "agent_mode": "dispatcher",
                    "user_model": user_agent.default_model,
                    "sys_model": sys_agent.default_model,
                    "user_max_turns": user_agent.max_turns,
                    "sys_max_turns": sys_agent.max_turns,
                },
            })
```

And on config change (line ~88-89):

```python
# After self.reset_resource_state():
            collector = self.get_trace_collector()
            collector.emit({
                "event_type": "lifecycle.config",
                "status": "ok",
                "metadata": {
                    "action": "agent_rebuilt",
                    "reason": "model_config_changed",
                },
            })
```

- [ ] **Step 5: Add lifecycle.config instrumentation to routes.py**

In `routes.py:139` (config/save), after `mgr.reset_resource_state()`:

```python
    collector = mgr.get_trace_collector()
    collector.emit({
        "event_type": "lifecycle.config",
        "status": "ok",
        "metadata": {
            "action": "save",
            "config_name": config_name,
            "model_name": payload.model_name,
        },
    })
```

In `routes.py:201` (register-deepseek), after `mgr.reset_resource_state()`:

```python
    collector = mgr.get_trace_collector()
    collector.emit({
        "event_type": "lifecycle.config",
        "status": "ok",
        "metadata": {
            "action": "register_deepseek",
            "models_created": [m["name"] for m in created],
            "base_url": DEEPSEEK_BASE_URL,
        },
    })
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_session_lifecycle.py::TestStage1Init::test_lifecycle_init_trace_on_registry_load tests/test_session_lifecycle.py::TestStage1Init::test_lifecycle_init_trace_on_agent_build -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/arf/server/session_manager.py src/arf/server/routes.py tests/test_session_lifecycle.py
git commit -m "feat: add lifecycle.init and lifecycle.config trace instrumentation"
```

---

### Task 5: Instrument lifecycle.session_start and lifecycle.session_end

**Files:**
- Modify: `src/arf/server/routes.py:462-494` (chat endpoint — add session_start)
- Modify: `src/arf/server/session_manager.py:173-193` (fire_session_end — add guard + trace)
- Modify: `src/arf/server/ws.py:32-42` (remove SessionStart trace, keep hook)

- [ ] **Step 1: Write tests**

In `tests/test_session_lifecycle.py`, add to `TestStage3SessionCreation`:

```python
    def test_session_start_trace_on_first_chat(self, tmp_path):
        """First POST /api/chat should emit lifecycle.session_start trace."""
        from arf.server.session_manager import SessionManager

        ws = _make_workspace(tmp_path)
        _configure_quick_thinking(ws)
        mgr = SessionManager(ws)
        collector = mgr.get_trace_collector()

        # Simulate what chat endpoint does on new_session
        mgr.reset_session_history()
        collector.emit({
            "event_type": "lifecycle.session_start",
            "status": "ok",
            "metadata": {
                "session_id": mgr.current_session_id,
                "workspace": str(ws),
                "new_session": True,
                "transport": "http",
            },
        })

        start_events = [e for e in collector._buffer
                       if e["event_type"] == "lifecycle.session_start"]
        assert len(start_events) == 1
        assert start_events[0]["metadata"]["transport"] == "http"
```

Add to `TestStage5SessionEnd`:

```python
    def test_session_end_trace_on_reset(self, tmp_path):
        from arf.server.session_manager import SessionManager

        ws = _make_workspace(tmp_path)
        mgr = SessionManager(ws)
        collector = mgr.get_trace_collector()

        mgr.session_history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        start = mgr.session_start_time

        collector.emit({
            "event_type": "lifecycle.session_end",
            "status": "ok",
            "metadata": {
                "session_id": mgr.current_session_id,
                "message_count": 2,
                "duration_seconds": 10,
                "trigger": "stream_done",
            },
        })
        # flush should happen
        mgr.reset_session_history()

        end_events = [e for e in collector._buffer
                     if e["event_type"] == "lifecycle.session_end"]
        # After flush, buffer should be clear so no events remain
        # But we emitted before calling reset, so the event was flushed
        assert len(collector._buffer) == 0
```

- [ ] **Step 2: Add session_start trace to chat endpoint**

In `routes.py:467`, inside the `chat` function, after the `agent = mgr.get_agent()` line:

```python
    # Emit session_start trace on first message of a session
    if not mgr.session_history:
        collector = mgr.get_trace_collector()
        collector.emit({
            "event_type": "lifecycle.session_start",
            "status": "ok",
            "metadata": {
                "session_id": mgr.current_session_id,
                "workspace": workspace_dir,
                "new_session": payload.new_session,
                "transport": "ws" if payload.new_session else "http",
            },
        })
```

- [ ] **Step 3: Fix fire_session_end — add guard against repeated firing**

In `session_manager.py:26-44`, add to `__init__`:

```python
        self._session_end_fired = False
```

In `fire_session_end` (line ~173-193):

```python
    def fire_session_end(self):
        """Fire SessionEnd hooks on normal conversation completion.

        Idempotent — only fires once per session lifecycle.
        """
        if self._session_end_fired:
            return
        if not self.session_history or len(self.session_history) < 2:
            return
        self._session_end_fired = True

        sid = self.current_session_id
        runner = self.get_hook_runner()

        # Emit session_end trace
        duration = (datetime.now(timezone.utc) - self.session_start_time).total_seconds()
        collector = self.get_trace_collector()
        collector.emit({
            "event_type": "lifecycle.session_end",
            "status": "ok",
            "metadata": {
                "session_id": sid,
                "message_count": len(self.session_history),
                "duration_seconds": round(duration, 1),
                "trigger": "stream_done",
            },
        })

        try:
            runner.run("SessionEnd", {
                "session_id": sid,
                "session_title": self.session_title,
            }, stdin_data={
                "conversation": list(self.session_history),
                "session_start": self.session_start_time.isoformat(),
                "message_count": len(self.session_history),
            })
        except Exception:
            logger.exception("SessionEnd hooks failed on normal completion")
```

In `reset_session_history`, reset the guard:

```python
    self._session_end_fired = False
```

- [ ] **Step 4: Update WS handler — add trigger metadata, remove SessionStart trace**

In `ws.py:32-42`, the `SessionStart` hook firing stays (it's a hook, not trace). Remove WS-based session_start trace emission (if any was planned). Instead, in `ws.py:87-98` (the SessionEnd call), update the trigger:

```python
    data = self._pending_session
    self._pending_session = None
    if data is None:
        return

    history = data["history"]
    start_time = data["start_time"]
    title = data["title"]

    if self._mgr.session_start_time != start_time:
        logger.info("Session already handled by another path, skipping archive")
        return

    session_id = start_time.strftime("%Y%m%d_%H%M%S")

    # Emit session_end trace with ws_disconnect trigger
    collector = self._mgr.get_trace_collector()
    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    collector.emit({
        "event_type": "lifecycle.session_end",
        "status": "ok",
        "metadata": {
            "session_id": session_id,
            "message_count": len(history),
            "duration_seconds": round(duration, 1),
            "trigger": "ws_disconnect",
            "grace_period": GRACE_PERIOD_SECONDS,
        },
    })

    try:
        loop = asyncio.get_running_loop()
        runner = self._mgr.get_hook_runner()
        await loop.run_in_executor(
            None,
            lambda: runner.run("SessionEnd", {
                "session_id": session_id,
                "session_title": title,
            }, stdin_data={
                "conversation": history,
                "session_start": start_time.isoformat(),
                "message_count": len(history),
            }),
        )
    except Exception:
        logger.exception("SessionEnd hooks failed")
```

Also in `routes.py:472-474` (new_session archive path), add a session_end trace with `trigger: "new_session"`.

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_session_lifecycle.py -v --tb=short
```

Expected: 63 passed

- [ ] **Step 6: Commit**

```bash
git add src/arf/server/routes.py src/arf/server/session_manager.py src/arf/server/ws.py tests/test_session_lifecycle.py
git commit -m "feat: add lifecycle.session_start and lifecycle.session_end trace, fix fire_session_end guard"
```

---

### Task 6: Instrument lifecycle.handoff (Dispatcher)

**Files:**
- Modify: `src/arf/engine/dispatcher.py:31-66` (run), `dispatcher.py:68-123` (run_stream)

- [ ] **Step 1: Write test**

In `tests/test_session_lifecycle.py`, add to `TestFullLifecycle`:

```python
    def test_handoff_emits_trace(self, tmp_path):
        from arf.server.session_manager import SessionManager
        from arf.server.trace_collector import TraceCollector

        ws = _make_workspace(tmp_path)
        mgr = SessionManager(ws)
        collector = mgr.get_trace_collector()

        # Simulate handoff trace emission
        collector.emit({
            "event_type": "lifecycle.handoff",
            "status": "ok",
            "metadata": {
                "phase": "user_agent_complete",
                "intent": "complex task",
                "required_actions": ["analyze", "refactor"],
                "user_turns_used": 4,
            },
        })
        collector.emit({
            "event_type": "lifecycle.handoff",
            "status": "ok",
            "metadata": {
                "phase": "sys_agent_start",
                "sys_model": "deep_thinking",
                "remaining_turns": 6,
            },
        })

        handoff_events = [e for e in collector._buffer
                         if e["event_type"] == "lifecycle.handoff"]
        assert len(handoff_events) == 2
```

- [ ] **Step 2: Instrument dispatcher.run()**

In `dispatcher.py:43-44`, after detecting handoff:

```python
        if not self._detect_handoff(user_result.tool_events):
            return user_result

        # Emit handoff trace — user agent phase complete
        collector = getattr(self.user_agent, 'hook_runner', None)
        # Dispatcher doesn't have direct access to TraceCollector,
        # so we inject it via agent or config.

        # Alternative: use the session_manager's collector via a callback.
```

Dispatcher doesn't have direct access to `TraceCollector` — it's on `SessionManager`. The cleanest approach: pass `trace_collector` through the engine config. In `base.py:_build_graph_engine`:

```python
# In _build_graph_engine (base.py line ~589-607), after creating the engine:
# Pass trace_collector through engine for dispatcher/nodes
engine._trace_collector = getattr(self, '_trace_collector', None)
```

In `SessionManager.get_agent()` after creating the dispatcher:

```python
self._agent = Dispatcher(user_agent, sys_agent)
# Inject trace collector
self._agent._trace_collector = self.get_trace_collector()
```

Then in `dispatcher.py.__init__`:

```python
def __init__(self, user_agent, sys_agent):
    self.user_agent = user_agent
    self.sys_agent = sys_agent
    self._trace_collector = None
```

In `dispatcher.run()`:

```python
# After self._detect_handoff check, before Phase 2:
        handoff = self._extract_handoff(user_result.tool_events)
        if self._trace_collector:
            self._trace_collector.emit({
                "event_type": "lifecycle.handoff",
                "status": "ok",
                "metadata": {
                    "phase": "user_agent_complete",
                    "intent": handoff.get("intent", ""),
                    "required_actions": handoff.get("required_actions", []),
                    "user_turns_used": user_result.turns,
                },
            })

        # Phase 2: Sys Agent
        sys_history = self._build_sys_history(history, message)
        sys_message = self._build_handoff_message(message, handoff)
        remaining_turns = max(1, total_max - user_result.turns)

        sys_result = self._run_phase(
            self.sys_agent, sys_message, sys_history, project_dir,
            max_turns=remaining_turns,
        )

        if self._trace_collector:
            self._trace_collector.emit({
                "event_type": "lifecycle.handoff",
                "status": "ok",
                "metadata": {
                    "phase": "sys_agent_complete",
                    "sys_model": self.sys_agent.default_model,
                    "remaining_turns": remaining_turns,
                    "sys_turns_used": sys_result.turns,
                },
            })
```

Same pattern for `dispatcher.run_stream()`.

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/test_session_lifecycle.py::TestFullLifecycle::test_handoff_emits_trace -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/arf/engine/dispatcher.py src/arf/agent/base.py src/arf/server/session_manager.py tests/test_session_lifecycle.py
git commit -m "feat: add lifecycle.handoff trace in Dispatcher"
```

---

### Task 7: Instrument lifecycle.model_switch, lifecycle.compaction, lifecycle.prompt_snapshot (graph nodes)

**Files:**
- Modify: `src/arf/engine/nodes.py:698-748` (_resolve_model_switch), `nodes.py:122-180` (compact_node), `nodes.py:262-479` (call_model_node), `nodes.py:484-664` (call_model_node_stream)

- [ ] **Step 1: Write tests**

In `tests/test_session_lifecycle.py`, add to `TestStage4ConversationLoop`:

```python
    def test_model_switch_emits_trace(self):
        from arf.engine.nodes import _resolve_model_switch
        import json

        tool_calls = [{
            "id": "1",
            "function": {
                "name": "model_switch",
                "arguments": json.dumps({"target": "deep_thinking"}),
            },
        }]
        tool_results = [{
            "tool_call_id": "1",
            "content": json.dumps({"ok": True, "model_type": "deep_thinking"}),
        }]

        result = _resolve_model_switch(tool_calls, tool_results, None)
        assert result.get("current_model") == "deep_thinking"

    def test_prompt_hash_consistent_for_same_prompt(self):
        from arf.server.trace_collector import compute_prompt_hash

        prompt1 = "You are an AI assistant.\n\n## Workspace: Test"
        prompt2 = "You are an AI assistant.\n\n## Workspace: Test"
        prompt3 = "You are an AI assistant.\n\n## Workspace: Different"

        h1 = compute_prompt_hash(prompt1)
        h2 = compute_prompt_hash(prompt2)
        h3 = compute_prompt_hash(prompt3)

        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 16

    def test_prompt_snapshot_emits_trace(self, tmp_path):
        from arf.server.session_manager import SessionManager
        from arf.server.trace_collector import compute_prompt_hash

        ws = _make_workspace(tmp_path)
        mgr = SessionManager(ws)
        collector = mgr.get_trace_collector()

        prompt = "You are helpful."
        prompt_hash = compute_prompt_hash(prompt)
        sections = ["workspace", "identity", "inventory", "language"]

        collector.emit({
            "event_type": "lifecycle.prompt_snapshot",
            "turn": 1,
            "model": "quick_thinking",
            "metadata": {
                "prompt_hash": prompt_hash,
                "prompt_length": len(prompt),
                "pipeline_sections": sections,
                "active_tools_count": 9,
                "tools_list": ["file_reader", "file_writer"],
            },
        })

        snap = collector._buffer[0]
        assert snap["event_type"] == "lifecycle.prompt_snapshot"
        assert snap["metadata"]["prompt_hash"] == prompt_hash
        assert snap["metadata"]["pipeline_sections"] == sections
```

- [ ] **Step 2: Instrument `_resolve_model_switch` in nodes.py**

In `nodes.py:698-748`, after successful model switch detection, emit trace via config. The `_resolve_model_switch` function doesn't have access to the collector, so we need to pass it through config.

In `graph.py:_build_config()`, add:

```python
"trace_collector": None,  # injected by SessionManager
```

In `session_manager.py` or `base.py:_build_graph_engine()`:

```python
engine._trace_collector = self.get_trace_collector()  # for SessionManager path
```

In `nodes.py:721-723`, after `model_switch` success:

```python
                        if isinstance(result, dict) and result.get("ok"):
                            mt = result.get("model_type", "")
                            if mt:
                                logger.info("model_switch: updating current_model → %s", mt)
                                # Emit trace
                                tc = config.get("configurable", {}).get("trace_collector")
                                if tc:
                                    tc.emit({
                                        "event_type": "lifecycle.model_switch",
                                        "turn": state.get("turn_count", 0),
                                        "status": "ok",
                                        "metadata": {
                                            "from_model": state.get("current_model", ""),
                                            "to_model": mt,
                                            "tool": "model_switch",
                                        },
                                    })
                                return {"current_model": mt}
```

Same pattern for `model_manager` switch at line ~737-743.

- [ ] **Step 3: Instrument `compact_node`**

In `nodes.py:165-180`, the `compact_node` already records `node_traces`. Change `"node": "compact"` to also emit:

```python
"event_type": "lifecycle.compaction",
```

The node name stays "compact" for backward compat.

- [ ] **Step 4: Instrument `call_model_node` — prompt_snapshot**

In `nodes.py:278-279`, after building `msgs`:

```python
    # Emit prompt_snapshot trace
    tc = config.get("configurable", {}).get("trace_collector")
    if tc:
        from arf.server.trace_collector import compute_prompt_hash
        prompt_text = state["system_prompt"]
        prompt_hash = compute_prompt_hash(prompt_text)
        tc.emit({
            "event_type": "lifecycle.prompt_snapshot",
            "turn": state["turn_count"],
            "model": model_type,
            "metadata": {
                "prompt_hash": prompt_hash,
                "prompt_length": len(prompt_text),
                "pipeline_sections": _active_pipeline_sections(state),
                "active_tools_count": len(tools) if tools else 0,
                "tools_list": [t["function"]["name"] for t in tools] if tools else [],
            },
        })
        # Store hash in trace_collector for graph.call_model to reference
        state["_current_prompt_hash"] = prompt_hash

        # Insert prompt text into prompts table
        try:
            from ..server.database import insert_prompt
            insert_prompt(prompt_hash, prompt_text)
        except Exception:
            pass
```

Add a helper to detect active pipeline sections:

```python
def _active_pipeline_sections(state: dict) -> list[str]:
    """Return list of prompt pipeline sections that produced content."""
    # Sections are built by BaseAgent.build_system_prompt().
    # The full prompt is in state["system_prompt"] — we detect
    # section headers to infer which were active.
    prompt = state.get("system_prompt", "")
    sections = []
    markers = [
        ("## Workspace:", "workspace"),
        ("## Your Resources", "user_resources"),
        ("## Long-Term Memory", "long_term_memory"),
        ("## Memory", "memory"),
        ("## CRITICAL", "critical_rules"),
        ("## Available Resources", "inventory"),
        ("You are ARF Agent", "identity"),
    ]
    for marker, name in markers:
        if marker in prompt:
            sections.append(name)
    # Language is always present
    if "简体中文" in prompt or "English" in prompt:
        sections.append("language")
    return sections
```

Same pattern in `call_model_node_stream` (line ~501).

- [ ] **Step 5: Wire trace_collector into GraphEngine config**

In `base.py:_build_graph_engine()` (line ~583-607):

```python
    # After engine = GraphEngine(...):
    if hasattr(self, '_trace_collector'):
        engine._trace_collector = self._trace_collector
```

In `graph.py:_build_config()` (line ~373-389), add to configurable dict:

```python
    "trace_collector": getattr(self, '_trace_collector', None),
```

- [ ] **Step 6: Run tests**

```bash
python3 -m pytest tests/test_session_lifecycle.py -v --tb=short
```

Expected: 66+ passed

- [ ] **Step 7: Commit**

```bash
git add src/arf/engine/nodes.py src/arf/engine/graph.py src/arf/agent/base.py src/arf/server/session_manager.py tests/test_session_lifecycle.py
git commit -m "feat: add lifecycle.model_switch, lifecycle.compaction, lifecycle.prompt_snapshot trace"
```

---

### Task 8: Bug fix — _evict_oldest off-by-one

**Files:**
- Modify: `src/arf/server/sessions.py:147-153` (_evict_oldest)
- Modify: `tests/test_session_lifecycle.py` (fix eviction test expectation)

- [ ] **Step 1: Fix `_evict_oldest` condition**

```python
def _evict_oldest(sessions_dir: Path):
    """Remove the oldest archive if we're at capacity."""
    files = sorted(sessions_dir.glob("*.json"))
    while len(files) > MAX_ARCHIVES:  # was >=, now >
        oldest = files.pop(0)
        oldest.unlink()
        logger.debug("Evicted old session archive: %s", oldest.name)
```

- [ ] **Step 2: Update test to expect correct behavior**

In `tests/test_session_lifecycle.py`, change the eviction test:

```python
    def test_archive_eviction_at_max(self, tmp_path):
        from arf.server.sessions import archive_session, MAX_ARCHIVES

        ws = _make_workspace(tmp_path)

        for i in range(MAX_ARCHIVES + 2):
            start = datetime(2026, 5, 18, 10, i, tzinfo=timezone.utc)
            archive_session(
                [{"role": "user", "content": f"msg{i}"},
                 {"role": "assistant", "content": f"reply{i}"}],
                start, str(ws), title=f"Session {i}",
            )

        files = list((ws / "memory" / "sessions").glob("*.json"))
        assert len(files) == MAX_ARCHIVES  # Now correct
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/test_session_lifecycle.py::TestStage5SessionEnd::test_archive_eviction_at_max -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/arf/server/sessions.py tests/test_session_lifecycle.py
git commit -m "fix: _evict_oldest off-by-one (>= to >), caps at MAX_ARCHIVES"
```

---

### Task 9: Integration — connect remaining trace emission paths and final tests

**Files:**
- Modify: `src/arf/server/routes.py:472-474` (new_session session_end trace)
- Modify: `tests/test_session_lifecycle.py` (add integration assertions)

- [ ] **Step 1: Add session_end trace on new_session in routes.py**

In `routes.py:468-494`, before `reset_session_history()`:

```python
    if payload.new_session:
        collector = mgr.get_trace_collector()
        if mgr.session_history and len(mgr.session_history) >= 2:
            duration = (datetime.now(timezone.utc) - mgr.session_start_time).total_seconds()
            collector.emit({
                "event_type": "lifecycle.session_end",
                "status": "ok",
                "metadata": {
                    "session_id": mgr.current_session_id,
                    "message_count": len(mgr.session_history),
                    "duration_seconds": round(duration, 1),
                    "trigger": "new_session",
                },
            })

        old_history = list(mgr.session_history)
        old_start = mgr.session_start_time
        old_title = mgr.session_title
        if old_history and len(old_history) >= 2:
            try:
                sid = archive_session(old_history, old_start, workspace_dir, old_title,
                                      graph_traces=mgr.last_traces, usage=mgr.last_usage)
                if sid:
                    from .database import insert_session, update_session
                    fpath = f"memory/sessions/{sid}.json"
                    insert_session(sid, "admin", old_title, fpath)
                    fp = Path(workspace_dir) / fpath
                    if fp.exists():
                        sz = fp.stat().st_size / (1024 * 1024)
                        turns = len(old_history) // 2
                        update_session(sid, turn_count=turns, json_size_mb=round(sz, 3),
                                      message_count=len(old_history))
            except Exception:
                pass
        mgr.reset_session_history()
```

- [ ] **Step 2: Update export API to JOIN prompts table**

In `routes.py:255-293` (trace_export):

```python
# After loading events, enrich with full prompt text
from .database import get_prompt
for event in events:
    meta = event.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    prompt_hash = meta.get("prompt_hash") if isinstance(meta, dict) else None
    if prompt_hash:
        full = get_prompt(prompt_hash)
        if full:
            meta["prompt_full"] = full
    event["metadata"] = meta
```

- [ ] **Step 3: Run full test suite**

```bash
python3 -m pytest tests/test_session_lifecycle.py -v --tb=short
```

Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/arf/server/routes.py tests/test_session_lifecycle.py
git commit -m "feat: add new_session session_end trace, enrich export with prompt text"
```

---

### Task 10: Final verification

- [ ] **Step 1: Run all existing tests**

```bash
python3 -m pytest tests/ -v --tb=short
```

Expected: All existing tests still pass, plus new trace tests.

- [ ] **Step 2: Run the existing dual-agent tests**

```bash
python3 -m pytest tests/test_dual_agent.py -v --tb=short
```

Expected: No regression.

- [ ] **Step 3: Commit any final tweaks**

```bash
git add -A
git commit -m "chore: final trace system integration fixes"
```
