# Trace System Upgrade — Unified Lifecycle Event Instrumentation & Storage

## Goal

Upgrade ARF's trace system to uniformly instrument all session lifecycle events, consolidate storage to SQLite as the single source of truth, and enable prompt-level analysis for A/B testing.

## Background

Current state (from session lifecycle analysis 2026-05-18):

- **Dual-write**: SQLite `trace_events` + JSONL files in `memory/traces/` — no clear owner
- **Missing events**: INIT, API key config, session start/end, handoff, compaction have no structured trace
- **Inconsistent schema**: node traces have ad-hoc fields (`tool_name` vs `model` vs neither)
- **Session boundary bugs**: `fire_session_end()` fires on every stream `done`, not actual session end
- **Off-by-one bug**: `_evict_oldest` uses `>=` instead of `>`, caps at MAX_ARCHIVES - 1

## Design Decisions

| Decision | Choice |
|----------|--------|
| Storage | **SQLite only** — JSONL generated on export |
| Trace scope | **All 8 event types** — full lifecycle coverage |
| Prompt storage | **Separate `prompts` table** — hash in trace, full text in prompts |
| Backward compat | Old JSONL files kept, not deleted. Old trace rows readable via `node` field |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  Instrumentation Points                  │
│  (routes.py, ws.py, nodes.py, dispatcher.py,            │
│   session_manager.py, resources/manager.py)             │
└──────────────────────┬──────────────────────────────────┘
                       │ event dict
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   TraceCollector                         │
│  - In-memory buffer (list[dict])                        │
│  - Flush on session_end to SQLite                       │
│  - Thread-safe singleton per SessionManager             │
└──────────────────────┬──────────────────────────────────┘
                       │ flush: INSERT MANY
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  SQLite (arf.db)                         │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐ │
│  │ trace_events │  │   prompts     │  │   sessions   │ │
│  └──────────────┘  └───────────────┘  └──────────────┘ │
│         │                                       │        │
│         └───────────────────────────────────────┘        │
│                    JOIN on session_id / prompt_hash      │
└──────────────────────┬──────────────────────────────────┘
                       │ export request
                       ▼
               JSON / JSONL file
```

## Unified Trace Event Schema

```python
{
    "event_id": "uuid",
    "session_id": "20260518_100000",
    "timestamp": "2026-05-18T10:00:01.123Z",
    "event_type": str,          # see table below
    "node": str | None,          # graph node name, None for lifecycle events
    "turn": int | None,          # turn number, None for lifecycle events
    "status": "ok|error|skipped|blocked",
    "duration_ms": float | None,
    "model": str | None,
    "tool_name": str | None,
    "prompt_tokens": int,
    "completion_tokens": int,
    "total_tokens": int,
    "error_msg": str | None,
    "metadata": dict,            # per-event-type context (JSON)
}
```

## Event Types

| # | event_type | Trigger | node | metadata |
|---|-----------|---------|------|----------|
| 1 | `lifecycle.init` | Registry loaded, agent built | null | `{counts: {models, tools, skills}, source, agent_mode}` |
| 2 | `lifecycle.config` | API key registered/saved/tested | null | `{action, models_created, config_name, success}` |
| 3 | `lifecycle.session_start` | First POST /api/chat (unified) | null | `{session_id, workspace, new_session, transport}` |
| 4 | `lifecycle.session_end` | `reset_session_history()` | null | `{session_id, message_count, duration_seconds, trigger}` |
| 5 | `lifecycle.handoff` | Dispatcher user→sys agent | null | `{intent, required_actions, user_turns_used}` |
| 6 | `lifecycle.model_switch` | model_switch/model_manager ok | null | `{from_model, to_model, tool}` |
| 7 | `lifecycle.compaction` | compact_node done | "compact" | `{turns_compacted, tokens_before, threshold}` |
| 8 | `lifecycle.prompt_snapshot` | Before each call_model | null | `{prompt_hash, prompt_length, pipeline_sections, active_tools_count, tools_list, model, turn}` |
| - | `graph.classify` | classify_node (existing) | "classify" | `{classification, resolved_model}` |
| - | `graph.call_model` | call_model_node (existing) | "call_model" | `{finish_reason, has_tool_calls, prompt_hash, input_snippet, output_snippet}` |
| - | `graph.execute_tools` | execute_tools_node (existing) | "execute_tools" | `{tool_category, tool_input_snippet, tool_output_snippet}` |
| - | `graph.hook` | hook invoked (existing) | "hook" | `{hook_event, hook_status, tool_category}` |
| - | `graph.respond` | respond_node (existing) | "respond" | `{response_snippet, truncated}` |
| - | `graph.recovery` | recovery_node (existing) | "recovery" | `{recovery_type, error_snippet}` |

## New `prompts` Table

```sql
CREATE TABLE IF NOT EXISTS prompts (
    prompt_hash   TEXT PRIMARY KEY,
    prompt_full   TEXT NOT NULL,
    prompt_length INTEGER NOT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
);
```

Write logic: `INSERT OR IGNORE INTO prompts` before each `call_model_node` invocation.

## SQLite Schema Migration

```sql
ALTER TABLE trace_events ADD COLUMN event_type TEXT NOT NULL DEFAULT '';

-- Backfill: infer event_type from node field
UPDATE trace_events SET event_type = 'graph.call_model'     WHERE node = 'call_model';
UPDATE trace_events SET event_type = 'graph.execute_tools'  WHERE node = 'execute_tools';
UPDATE trace_events SET event_type = 'graph.hook'           WHERE node = 'hook';
UPDATE trace_events SET event_type = 'graph.classify'       WHERE node = 'classify';
UPDATE trace_events SET event_type = 'graph.respond'        WHERE node = 'respond';
UPDATE trace_events SET event_type = 'graph.recovery'       WHERE node = 'recovery';
UPDATE trace_events SET event_type = 'lifecycle.compaction' WHERE node = 'compact';
```

## Instrumentation Points

### lifecycle.init
- `session_manager.py:get_registry()` — registry load success/failure
- `session_manager.py:get_agent()` — agent construction

### lifecycle.config
- `routes.py:POST /api/config/register-deepseek` — bulk registration
- `routes.py:POST /api/config/save` — single save
- `routes.py:POST /api/config/test` — connection test
- `session_manager.py:get_agent()` — agent rebuild on config change

### lifecycle.session_start
- `routes.py:POST /api/chat` — unified entry (replaces ws.py-only SessionStart)
- Only fires once per `reset_session_history()` cycle

### lifecycle.session_end
- `session_manager.py:reset_session_history()` — fires once, before clearing state
- Trigger types: `ws_disconnect`, `new_session`, `stream_done`

### lifecycle.handoff
- `dispatcher.py:run()` / `run_stream()` — UserAgent→SysAgent transition
- Both phases recorded: user agent result + sys agent start

### lifecycle.model_switch
- `nodes.py:_resolve_model_switch()` — successful model switch only

### lifecycle.compaction
- `nodes.py:compact_node()` — when compaction actually runs

### lifecycle.prompt_snapshot
- `nodes.py:call_model_node()` / `call_model_node_stream()` — before model call
- `graph.call_model` trace also carries `prompt_hash` for correlation

## Storage Migration

1. Remove `_write_trace_file()` JSONL dual-write from `database.py`
2. `insert_trace_events()` → SQLite only
3. Keep existing JSONL files on disk (don't delete)
4. Export API reads from SQLite, generates JSON on demand

## Bug Fixes Included

- `_evict_oldest`: change `>= MAX_ARCHIVES` to `> MAX_ARCHIVES`
- `fire_session_end()`: guard with a boolean `_session_end_fired` to prevent per-done firing
- SessionStart: move from ws.py-only to unified routes.py entry

## Files Changed

| File | Change |
|------|--------|
| `src/arf/server/database.py` | Add `prompts` table, remove JSONL dual-write, add trace_collector flush |
| `src/arf/server/routes.py` | Add lifecycle.init/config/session_start/session_end instrumentation |
| `src/arf/server/ws.py` | Remove SessionStart trace from here (moved to routes.py) |
| `src/arf/server/session_manager.py` | Add TraceCollector, session_end instrumentation, fix fire_session_end |
| `src/arf/engine/nodes.py` | Add prompt_snapshot/model_switch/compaction instrumentation |
| `src/arf/engine/dispatcher.py` | Add handoff instrumentation |
| `src/arf/server/sessions.py` | Fix _evict_oldest off-by-one |
| `tests/test_session_lifecycle.py` | Add trace collection assertions |
