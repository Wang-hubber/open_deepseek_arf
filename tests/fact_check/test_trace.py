"""Fact-check tests: Trace/Observability Domain — docs/trace.md vs arf/observability/ + arf/streaming/.

Each test validates a specific claim made in the documentation against actual code.
PASS = doc/code consistent. FAIL = discrepancy found (fact-check finding).

TDD-style: the doc IS the spec; the test asserts the spec is met.
"""

import json
import inspect
import sys
from pathlib import Path
from typing import get_args

import pytest

# ---------------------------------------------------------------------------
# 2.1 Architecture Overview — EventBus flow and sinks
# ---------------------------------------------------------------------------

class TestArchitectureOverview:
    """Doc 2.1: GraphEngine._emit() / _make_event() inject round and
    publish to EventBus. Four sinks: FileTraceStore, UsageTracker, SSE, TraceView."""

    def test_engine_has_emit_method(self):
        """Doc: GraphEngine has _emit() method for publishing events."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "_emit")
        assert callable(GraphEngine._emit)

    def test_engine_has_make_event_method(self):
        """Doc: GraphEngine has _make_event() method (used by astream)."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "_make_event")
        assert callable(GraphEngine._make_event)

    def test_emit_injects_round_from_interaction_round(self):
        """Doc: _emit() injects data.round from AgentState.interaction_round."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine._emit)
        assert 'data["round"] = self._interaction_round' in src

    def test_make_event_injects_round_from_interaction_round(self):
        """Doc: _make_event() also injects data.round from interaction_round."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine._make_event)
        assert 'data["round"] = self._interaction_round' in src

    def test_emit_publishes_to_event_bus(self):
        """Doc: _emit calls self.event_bus.emit(AgentEvent(...))."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine._emit)
        assert "self.event_bus.emit(AgentEvent(" in src

    def test_interaction_round_init_at_zero(self):
        """Doc: _interaction_round starts at 0 in __init__."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.__init__)
        assert "self._interaction_round = 0" in src

    def test_interaction_round_reads_from_state_in_invoke(self):
        """Doc: _interaction_round read from state['interaction_round']."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert 'state.get("interaction_round", 0)' in src

    def test_interaction_round_reads_from_state_in_astream(self):
        """Doc: _interaction_round read from state in astream too."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.astream)
        assert 'state.get("interaction_round", 0)' in src

    def test_base_agent_increments_interaction_round(self):
        """Doc: interaction_round is per user message +1 in BaseAgent.chat()."""
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.chat)
        assert 'interaction_round"' in src
        assert '"interaction_round": interaction' in src


# ---------------------------------------------------------------------------
# 2.2 Event Model — AgentEvent and EventType
# ---------------------------------------------------------------------------

class TestEventModel:
    """Doc 2.2: AgentEvent fields — type, data, timestamp, trace_id,
    span_id, session_id, turn. EventType is a Literal of all event types."""

    def test_agent_event_has_seven_fields(self):
        """Doc: AgentEvent has type, data, timestamp, trace_id, span_id,
        session_id, turn (and parent_span_id, agent_name). Actually has 9."""
        from arf.core.events import AgentEvent
        fields = {f.name for f in AgentEvent.__dataclass_fields__.values()}
        for f in ("type", "data", "timestamp", "trace_id", "span_id",
                   "session_id", "turn"):
            assert f in fields, f"AgentEvent missing field: {f}"

    def test_agent_event_default_timestamp_is_time(self):
        """Doc: timestamp defaults to time.time()."""
        from arf.core.events import AgentEvent
        import time
        e = AgentEvent(type="session_start")
        assert isinstance(e.timestamp, float)
        # Should be very close to now
        assert abs(e.timestamp - time.time()) < 5

    def test_agent_event_data_defaults_to_empty_dict(self):
        """Doc: data defaults to empty dict."""
        from arf.core.events import AgentEvent
        e = AgentEvent(type="session_start")
        assert e.data == {}

    def test_event_type_is_literal(self):
        """Doc: EventType is a Literal type."""
        from arf.core.events import EventType
        from typing import Literal
        origin = getattr(EventType, "__origin__", None)
        assert origin is Literal, "EventType is not a Literal type"

    def test_doc_event_types_present_in_literal(self):
        """Doc table lists 18 specific event types. Verify all are in EventType."""
        from arf.core.events import EventType
        args = get_args(EventType)
        doc_types = {
            "session_start", "session_end", "user_input",
            "model_call_start", "model_call_end", "thinking_delta",
            "tool_call_start", "tool_call_end",
            "compaction_start", "compaction_end",
            "guard_block", "guard_pass",
            "approval_required", "approval_resolved",
            "hook_start", "hook_end",
            "error",
        }
        for t in doc_types:
            assert t in args, f"Event type '{t}' from doc not in EventType literal"

    def test_doc_event_types_have_no_spelling_mismatches(self):
        """Doc claims guard_block/guard_pass. Verify code has exact same spelling."""
        from arf.core.events import EventType
        args = set(get_args(EventType))
        assert "guard_block" in args
        assert "guard_pass" in args
        assert "approval_required" in args
        assert "approval_resolved" in args


# ---------------------------------------------------------------------------
# 2.2 Discrepancy: Event types in code but NOT in doc table
# ---------------------------------------------------------------------------

class TestEventTypesNotInDoc:
    """Cross-check: EventType literal vs doc table — finds undocumented types."""

    def test_agent_switch_not_in_doc(self):
        """Doc table omits agent_switch (exists in EventType literal)."""
        from arf.core.events import EventType
        args = get_args(EventType)
        assert "agent_switch" in args, (
            "FACT: agent_switch exists in code EventType but doc table omits it"
        )

    def test_undo_executed_not_in_doc(self):
        """Doc table omits undo_executed (exists in EventType literal)."""
        from arf.core.events import EventType
        assert "undo_executed" in get_args(EventType)

    def test_rollback_executed_not_in_doc(self):
        """Doc table omits rollback_executed (exists in EventType literal)."""
        from arf.core.events import EventType
        assert "rollback_executed" in get_args(EventType)

    def test_protection_events_not_in_doc(self):
        """Doc table omits 5 protection events (rate_limited, circuit_*,
        breaker_blocked)."""
        from arf.core.events import EventType
        args = get_args(EventType)
        for t in ("rate_limited", "circuit_opened", "circuit_half_open",
                   "circuit_closed", "breaker_blocked"):
            assert t in args, f"Protection event '{t}' exists in code but not doc table"


# ---------------------------------------------------------------------------
# 2.3 FileTraceStore
# ---------------------------------------------------------------------------

class TestFileTraceStoreInit:
    """Doc 2.3: FileTraceStore in arf/observability/file_trace.py."""

    def test_file_trace_store_exists(self):
        """Doc: arf/observability/file_trace.py contains FileTraceStore."""
        from arf.observability.file_trace import FileTraceStore
        assert FileTraceStore is not None

    def test_init_takes_bus_and_dir_with_default(self):
        """Doc: FileTraceStore(bus, dir) with default dir='./memory/sessions'."""
        from arf.observability.file_trace import FileTraceStore
        sig = inspect.signature(FileTraceStore.__init__)
        params = sig.parameters
        assert "bus" in params
        assert "dir" in params
        assert params["dir"].default == "./memory/sessions"

    def test_init_uses_asyncio_create_task(self):
        """Doc: Uses asyncio.create_task to subscribe to EventBus."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore.__init__)
        assert "asyncio.create_task" in src

    def test_init_creates_directory(self):
        """Doc: dir path is created on init."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore.__init__)
        assert "mkdir" in src


class TestFileTraceStoreFiltering:
    """Doc 2.3: Filter rules — session_start, session_end, thinking_delta skipped.
    guard_block, guard_pass, approval_required, approval_resolved persisted."""

    def test_skips_session_start(self):
        """Doc: session_start is filtered out (not written to disk)."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore._consume)
        assert '"session_start"' in src
        assert '"session_start"' in src.split("continue")[0]

    def test_skips_session_end(self):
        """Doc: session_end is filtered out."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore._consume)
        assert '"session_end"' in src

    def test_skips_thinking_delta(self):
        """Doc: thinking_delta is filtered out."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore._consume)
        assert '"thinking_delta"' in src

    def test_filter_skip_set_has_exactly_three_types(self):
        """Doc: exactly three types are skipped (session_start, session_end,
        thinking_delta)."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore._consume)
        # Find the conditional that checks for skip types
        assert 'event.type in ("session_start", "session_end", "thinking_delta")' in src

    def test_guard_block_not_filtered(self):
        """Doc: guard_block is persisted (not in skip set)."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore._consume)
        # guard_block should NOT appear in the continue line
        continue_line = src.split("continue")[0]
        assert "guard_block" not in continue_line

    def test_guard_pass_not_filtered(self):
        """Doc: guard_pass is persisted."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore._consume)
        continue_line = src.split("continue")[0]
        assert "guard_pass" not in continue_line

    def test_approval_required_not_filtered(self):
        """Doc: approval_required is persisted."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore._consume)
        continue_line = src.split("continue")[0]
        assert "approval_required" not in continue_line

    def test_approval_resolved_not_filtered(self):
        """Doc: approval_resolved is persisted."""
        from arf.observability.file_trace import FileTraceStore
        src = inspect.getsource(FileTraceStore._consume)
        continue_line = src.split("continue")[0]
        assert "approval_resolved" not in continue_line


class TestFileTraceStoreMethods:
    """Doc 2.3: FileTraceStore.load(session_id) and list_sessions()."""

    def test_load_exists(self):
        """Doc: load(session_id) returns list of events for a session."""
        from arf.observability.file_trace import FileTraceStore
        assert hasattr(FileTraceStore, "load")
        assert callable(FileTraceStore.load)

    def test_load_accepts_session_id(self):
        """Doc: load(session_id)."""
        from arf.observability.file_trace import FileTraceStore
        sig = inspect.signature(FileTraceStore.load)
        assert "session_id" in sig.parameters

    def test_list_sessions_exists(self):
        """Doc: list_sessions() returns list of session IDs."""
        from arf.observability.file_trace import FileTraceStore
        assert hasattr(FileTraceStore, "list_sessions")
        assert callable(FileTraceStore.list_sessions)

    def test_append_writes_to_correct_path(self):
        """Doc: events written to {dir}/{session_id}.json."""
        import asyncio, tempfile
        from arf.observability.file_trace import FileTraceStore
        from arf.core.events import AgentEvent
        from arf.event_bus import InMemoryEventBus

        async def run():
            with tempfile.TemporaryDirectory() as d:
                bus = InMemoryEventBus()
                store = FileTraceStore(bus, dir=d)
                # Simulate a non-filtered event directly through internal method
                store._append("test_sess", AgentEvent(
                    type="model_call_end", data={"usage": {"total_tokens": 10}},
                    session_id="test_sess"
                ))
                path = Path(d) / "test_sess.json"
                assert path.exists(), f"Expected file at {path}"
                data = json.loads(path.read_text(encoding="utf-8"))
                assert len(data) == 1
                assert data[0]["type"] == "model_call_end"

        asyncio.run(run())

    def test_append_accumulates_multiple_events(self):
        """Doc: file is append-style (reads, extends, writes)."""
        import asyncio, tempfile
        from arf.observability.file_trace import FileTraceStore
        from arf.core.events import AgentEvent

        async def run():
            with tempfile.TemporaryDirectory() as d:
                store = FileTraceStore.__new__(FileTraceStore)
                store._dir = Path(d)
                store._append("s1", AgentEvent(type="model_call_start", data={}, session_id="s1"))
                store._append("s1", AgentEvent(type="model_call_end", data={}, session_id="s1"))
                data = store.load("s1")
                assert len(data) == 2

        asyncio.run(run())

    def test_load_returns_empty_list_for_missing_session(self):
        """Doc: load returns [] for non-existent session."""
        import tempfile
        from arf.observability.file_trace import FileTraceStore
        store = FileTraceStore.__new__(FileTraceStore)
        store._dir = Path(tempfile.mkdtemp())
        result = store.load("nonexistent")
        assert result == []


# ---------------------------------------------------------------------------
# 2.4 UsageTracker
# ---------------------------------------------------------------------------

class TestUsageTracker:
    """Doc 2.4: UsageTracker in arf/observability/usage_tracker.py."""

    def test_usage_tracker_exists(self):
        """Doc: arf/observability/usage_tracker.py contains UsageTracker."""
        from arf.observability.usage_tracker import UsageTracker
        assert UsageTracker is not None

    def test_init_takes_bus_and_dir_with_default(self):
        """Doc: UsageTracker(bus, dir='./memory')."""
        from arf.observability.usage_tracker import UsageTracker
        sig = inspect.signature(UsageTracker.__init__)
        params = sig.parameters
        assert "bus" in params
        assert "dir" in params
        assert params["dir"].default == "./memory"

    def test_persists_to_usage_json(self):
        """Doc: persists to memory/usage.json (code: {dir}/usage.json)."""
        from arf.observability.usage_tracker import UsageTracker
        src = inspect.getsource(UsageTracker.__init__)
        assert '"usage.json"' in src or "'usage.json'" in src

    def test_subscribes_to_model_call_end(self):
        """Doc: subscribes to model_call_end events."""
        from arf.observability.usage_tracker import UsageTracker
        src = inspect.getsource(UsageTracker._consume)
        assert '"model_call_end"' in src

    def test_accumulates_prompt_tokens(self):
        """Doc: accumulates prompt_tokens from usage data."""
        from arf.observability.usage_tracker import UsageTracker
        src = inspect.getsource(UsageTracker._consume)
        assert '"prompt_tokens"' in src

    def test_accumulates_completion_tokens(self):
        """Doc: accumulates completion_tokens from usage data."""
        from arf.observability.usage_tracker import UsageTracker
        src = inspect.getsource(UsageTracker._consume)
        assert '"completion_tokens"' in src

    def test_accumulates_total_tokens(self):
        """Doc: accumulates total_tokens from usage data."""
        from arf.observability.usage_tracker import UsageTracker
        src = inspect.getsource(UsageTracker._consume)
        assert '"total_tokens"' in src

    def test_accumulates_calls(self):
        """Doc: tracks per-model call count."""
        from arf.observability.usage_tracker import UsageTracker
        src = inspect.getsource(UsageTracker._consume)
        assert '"calls"' in src

    def test_summary_returns_dict_with_three_keys(self):
        """Doc: summary() returns total_tokens, total_calls, by_model."""
        from arf.observability.usage_tracker import UsageTracker
        sig = inspect.signature(UsageTracker.summary)
        ret = """UsageTracker(...).summary()"""  # Can't call without bus
        # We can verify the method exists and its source
        src = inspect.getsource(UsageTracker.summary)
        assert "total_tokens" in src
        assert "total_calls" in src
        assert "by_model" in src

    def test_save_load_roundtrip(self):
        """Doc: usage.json loaded on startup, survives restarts."""
        import asyncio, tempfile, time
        from arf.observability.usage_tracker import UsageTracker
        from arf.core.events import AgentEvent

        async def run():
            with tempfile.TemporaryDirectory() as d:
                # Manually create persisted state
                Path(d, "usage.json").write_text(json.dumps({
                    "models": {"deep": {"prompt_tokens": 100, "completion_tokens": 50,
                                          "total_tokens": 150, "calls": 2}},
                    "total_calls": 2,
                    "updated_at": time.time(),
                }), encoding="utf-8")
                # Create tracker without event bus
                tracker = object.__new__(UsageTracker)
                tracker._dir = Path(d)
                tracker._path = Path(d) / "usage.json"
                tracker._models = {}
                tracker._total_calls = 0
                tracker._load()
                assert tracker.total_calls == 2
                assert tracker.total_tokens == 150
                assert tracker.by_model[0]["model_name"] == "deep"

        asyncio.run(run())

    def test_base_agent_auto_creates_usage_tracker(self):
        """Doc: BaseAgent自动创建UsageTracker."""
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)
        assert "UsageTracker" in src
        assert "self._usage_tracker = UsageTracker(event_bus)" in src

    def test_usage_tracker_exported_from_observability(self):
        """Doc: UsageTracker is importable from arf.observability."""
        from arf.observability import UsageTracker
        assert UsageTracker is not None

    def test_by_model_returns_list_with_four_tracking_fields(self):
        """Doc: per-model stats include prompt_tokens, completion_tokens,
        total_tokens, calls."""
        from arf.observability.usage_tracker import UsageTracker
        # Check the by_model property
        src = inspect.getsource(UsageTracker.by_model.fget)
        for field in ("prompt_tokens", "completion_tokens", "total_tokens", "calls"):
            assert f'"{field}"' in src or f"'{field}'" in src


# ---------------------------------------------------------------------------
# 2.8 FileReplayController
# ---------------------------------------------------------------------------

class TestReplayController:
    """Doc 2.8: FileReplayController in arf/observability/replay.py."""

    def test_replay_controller_exists(self):
        """Doc: FileReplayController in arf/observability/replay.py."""
        from arf.observability.replay import FileReplayController
        assert FileReplayController is not None

    def test_has_start_recording(self):
        """Doc: start_recording(session_id) method."""
        from arf.observability.replay import FileReplayController
        assert hasattr(FileReplayController, "start_recording")
        assert callable(FileReplayController.start_recording)

    def test_has_record_model_output(self):
        """Doc: record_model_output(session_id, turn, model_name, output)."""
        from arf.observability.replay import FileReplayController
        sig = inspect.signature(FileReplayController.record_model_output)
        params = sig.parameters
        for p in ("session_id", "turn", "model_name", "output"):
            assert p in params

    def test_has_record_tool_result(self):
        """Doc: record_tool_result(session_id, turn, tool_name, params, result)."""
        from arf.observability.replay import FileReplayController
        sig = inspect.signature(FileReplayController.record_tool_result)
        params = sig.parameters
        for p in ("session_id", "turn", "tool_name", "params", "result"):
            assert p in params

    def test_has_stop_recording(self):
        """Doc: stop_recording() returns ReplayTrace."""
        from arf.observability.replay import FileReplayController
        assert hasattr(FileReplayController, "stop_recording")

    def test_has_replay(self):
        """Doc: replay(trace, *, start_turn, breakpoints)."""
        from arf.observability.replay import FileReplayController
        assert hasattr(FileReplayController, "replay")

    def test_replay_accepts_start_turn(self):
        """Doc: replay supports start_turn parameter."""
        from arf.observability.replay import FileReplayController
        sig = inspect.signature(FileReplayController.replay)
        assert "start_turn" in sig.parameters

    def test_replay_start_turn_defaults_to_zero(self):
        """Doc: start_turn defaults to 0."""
        from arf.observability.replay import FileReplayController
        sig = inspect.signature(FileReplayController.replay)
        assert sig.parameters["start_turn"].default == 0

    def test_replay_accepts_breakpoints(self):
        """Doc: replay supports breakpoints parameter (list[int]|None)."""
        from arf.observability.replay import FileReplayController
        sig = inspect.signature(FileReplayController.replay)
        assert "breakpoints" in sig.parameters

    def test_replay_breakpoints_defaults_none(self):
        """Doc: breakpoints defaults to None."""
        from arf.observability.replay import FileReplayController
        sig = inspect.signature(FileReplayController.replay)
        assert sig.parameters["breakpoints"].default is None

    def test_replay_yields_agent_event(self):
        """Doc: replay yields AgentEvent objects."""
        from arf.observability.replay import FileReplayController
        from arf.core.events import AgentEvent
        from arf.core.protocols.replay import ReplayTrace, TurnRecord
        import asyncio

        async def run():
            ctrl = FileReplayController()
            trace = ReplayTrace(session_id="s1", agent_config_hash="", arf_version="1.0")
            trace.turns.append(TurnRecord(turn=0, model_name="deep", model_output="hello"))
            results = []
            async for event in ctrl.replay(trace):
                results.append(event)
            assert len(results) >= 1
            assert isinstance(results[0], AgentEvent)
            assert results[0].type == "model_call_end"

        asyncio.run(run())

    def test_record_model_result_appends_tool_calls(self):
        """Doc: tool calls are recorded as part of the turn."""
        from arf.observability.replay import FileReplayController
        from arf.core.protocols.replay import ReplayTrace
        import asyncio

        async def run():
            ctrl = FileReplayController()
            await ctrl.start_recording("s1")
            await ctrl.record_model_output("s1", 0, "deep", "thinking")
            await ctrl.record_tool_result("s1", 0, "search", {"q": "x"}, {"r": "y"})
            trace = await ctrl.stop_recording()
            assert len(trace.turns) == 1
            assert len(trace.turns[0].tool_calls) == 1
            assert trace.turns[0].tool_calls[0]["tool_name"] == "search"

        asyncio.run(run())

    def test_replay_controller_exported(self):
        """Doc: FileReplayController exported from arf.observability."""
        from arf.observability import FileReplayController
        assert FileReplayController is not None


# ---------------------------------------------------------------------------
# 2.9 OpenTelemetry Module
# ---------------------------------------------------------------------------

class TestOtelTracer:
    """Doc 2.9: arf/observability/otel.py — framework code only, not connected to EventBus."""

    def test_otel_file_exists(self):
        """Doc: arf/observability/otel.py exists."""
        from arf.observability.otel import OtelTracer
        assert OtelTracer is not None

    def test_otel_has_consume(self):
        """Doc: OtelTracer has consume(events) method."""
        from arf.observability.otel import OtelTracer
        sig = inspect.signature(OtelTracer.consume)
        assert "events" in sig.parameters

    def test_otel_has_flush(self):
        """Doc: OtelTracer has flush() method."""
        from arf.observability.otel import OtelTracer
        assert hasattr(OtelTracer, "flush")

    def test_otel_not_connected_to_event_bus(self):
        """Doc: otel.py is NOT connected to EventBus (framework code only).
        Verify no EventBus import or subscription exists in the file."""
        otel_path = Path(__file__).parent.parent.parent / "arf" / "observability" / "otel.py"
        content = otel_path.read_text(encoding="utf-8")
        # Should NOT import EventBus or set up subscription
        assert "EventBus" not in content, (
            "OtelTracer should NOT reference EventBus (doc says framework code only)"
        )
        assert "subscribe" not in content, (
            "OtelTracer should NOT subscribe to EventBus"
        )

    def test_otel_uses_otel_exporter_env(self):
        """Doc: OtelTracer reads OTEL_EXPORTER environment variable."""
        from arf.observability.otel import OtelTracer
        src = inspect.getsource(OtelTracer.__init__)
        assert '"OTEL_EXPORTER"' in src or "'OTEL_EXPORTER'" in src


# ---------------------------------------------------------------------------
# 2.6 Trace API Endpoints
# ---------------------------------------------------------------------------

class TestTraceEndpoints:
    """Doc 2.6: API endpoints table — verify each exists in route files."""

    @staticmethod
    def _route_in_file(file_path, route_pattern):
        """Check if a FastAPI route decorator with the given path exists in file."""
        content = file_path.read_text(encoding="utf-8")
        # FastAPI decorators: @router.get("/path") or @router.post("/path")
        for prefix in ('@router.get(', '@router.post(', '@router.put(', '@router.delete('):
            marker = prefix + route_pattern
            if marker in content:
                return True
        return False

    def test_get_api_trace_exists(self):
        """Doc: GET /api/trace — full event list."""
        p = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "routers" / "trace.py"
        assert self._route_in_file(p, '"/api/trace"'), "/api/trace route not found"

    def test_get_api_traces_sessions_exists(self):
        """Doc: GET /api/traces/sessions — session list."""
        p = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "routers" / "trace.py"
        assert self._route_in_file(p, '"/api/traces/sessions"'), "Route not found"

    def test_get_api_traces_sessions_id_exists(self):
        """Doc: GET /api/traces/sessions/{id} — session detail."""
        p = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "routers" / "trace.py"
        assert self._route_in_file(p, '"/api/traces/sessions/'), "Route not found"

    def test_get_api_traces_summary_exists(self):
        """Doc: GET /api/traces/summary — statistics."""
        p = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "routers" / "trace.py"
        assert self._route_in_file(p, '"/api/traces/summary"'), "Route not found"

    def test_get_api_traces_resource_stats_exists(self):
        """Doc: GET /api/traces/resource-stats — tool/model stats."""
        p = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "routers" / "misc.py"
        assert self._route_in_file(p, '"/api/traces/resource-stats"'), "Route not found"

    def test_get_api_traces_export_exists(self):
        """Doc: GET /api/traces/export — raw JSON download."""
        p = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "routers" / "misc.py"
        assert self._route_in_file(p, '"/api/traces/export"'), "Route not found"

    def test_get_api_trace_stream_exists(self):
        """Doc: GET /api/trace/stream — SSE real-time push."""
        p = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "routers" / "trace.py"
        assert self._route_in_file(p, '"/api/trace/stream"'), "Route not found"


# ---------------------------------------------------------------------------
# 2.7 Trace Viewer
# ---------------------------------------------------------------------------

class TestTraceViewer:
    """Doc 2.7: /trace-viewer — single-file HTML, loads from /api/traces/sessions/default."""

    def test_trace_viewer_route_exists(self):
        """Doc: /trace-viewer route in trace.py."""
        p = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "routers" / "trace.py"
        content = p.read_text(encoding="utf-8")
        assert '@router.get("/trace-viewer")' in content, "/trace-viewer route not found"

    def test_trace_viewer_returns_html_file(self):
        """Doc: /trace-viewer returns the trace_viewer.html file."""
        trace_router_path = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "routers" / "trace.py"
        content = trace_router_path.read_text(encoding="utf-8")
        assert "trace_viewer.html" in content
        assert "FileResponse" in content

    def test_trace_viewer_html_exists(self):
        """Doc: trace_viewer.html exists in arf/observability/."""
        viewer_path = Path(__file__).parent.parent.parent / "arf" / "observability" / "trace_viewer.html"
        assert viewer_path.exists(), "trace_viewer.html not found in arf/observability/"

    def test_trace_viewer_fetches_from_sessions_default(self):
        """Doc: trace_viewer.html loads from /api/traces/sessions/default via fetch."""
        viewer_path = Path(__file__).parent.parent.parent / "arf" / "observability" / "trace_viewer.html"
        content = viewer_path.read_text(encoding="utf-8")
        assert "api/traces/sessions/default" in content

    def test_trace_viewer_is_single_file_html(self):
        """Doc: single file HTML, zero external dependencies (no <link> to external CSS)."""
        viewer_path = Path(__file__).parent.parent.parent / "arf" / "observability" / "trace_viewer.html"
        content = viewer_path.read_text(encoding="utf-8")
        # All CSS is inline; no external stylesheet links
        assert "<style>" in content
        # No external resource URLs (allow '//' for JS comments and data URIs)
        import re
        external_refs = re.findall(r'(?:src|href)\s*=\s*["\']https?://', content)
        assert len(external_refs) == 0, f"Found external resource URLs: {external_refs}"


# ---------------------------------------------------------------------------
# 2.10 Configuration — server.py and BaseAgent auto-creation
# ---------------------------------------------------------------------------

class TestTraceConfig:
    """Doc 2.10: FileTraceStore created in server.py; UsageTracker by BaseAgent."""

    def test_file_trace_store_created_in_server(self):
        """Doc: FileTraceStore created in server.py with agent.event_bus and trace_dir."""
        server_path = Path(__file__).parent.parent.parent / "app" / "arf_default_assistant" / "server.py"
        content = server_path.read_text(encoding="utf-8")
        assert "FileTraceStore" in content
        assert "event_bus" in content
        assert "trace_dir" in content

    def test_app_context_trace_dir_is_memory_traces(self):
        """Doc: trace_dir resolves to ./memory/traces/."""
        from arf.agent.app_context import AppContext
        from pathlib import Path
        ctx = AppContext(root=Path("/test"))
        assert str(ctx.trace_dir) == "/test/memory/traces"

    def test_base_agent_holds_event_bus(self):
        """Doc: BaseAgent creates event_bus and holds it."""
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)
        assert "event_bus = override_protocols.pop(" in src or "event_bus=" in src

    def test_base_agent_has_event_bus_property(self):
        """Doc: BaseAgent exposes event_bus property."""
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.event_bus.fget)
        assert "self._event_bus" in src

    def test_event_bus_created_by_default(self):
        """Doc: EventBus is auto-created when not overridden."""
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)
        assert "InMemoryEventBus()" in src


# ---------------------------------------------------------------------------
# SseStream (streaming module referenced by architecture)
# ---------------------------------------------------------------------------

class TestSseStream:
    """Doc 2.1 references SSE stream as a sink.
    Verify SseStream exists and implements the expected interface."""

    def test_sse_stream_exists(self):
        """Doc: SSE stream sink references SseStream."""
        from arf.streaming import SseStream
        assert SseStream is not None

    def test_sse_stream_has_publish(self):
        """Doc: SseStream.publish(event) sends data as SSE format."""
        from arf.streaming.adapters.sse import SseStream
        from arf.core.events import AgentEvent
        import asyncio

        async def run():
            stream = SseStream()
            # publish should not raise
            event = AgentEvent(type="test", data={"key": "val"})
            await stream.publish(event)

        asyncio.run(run())

    def test_sse_stream_has_listen(self):
        """Doc: SseStream.listen() is an async context manager for consumers."""
        from arf.streaming.adapters.sse import SseStream
        assert hasattr(SseStream, "listen")

    def test_sse_publish_formats_sse_protocol(self):
        """Doc: publish formats as 'data: {...}\n\n'."""
        from arf.streaming.adapters.sse import SseStream
        from arf.core.events import AgentEvent
        import asyncio

        async def run():
            stream = SseStream()
            received = []

            async def collect():
                async with stream.listen() as q:
                    async for msg in q:
                        received.append(msg)
                        break

            async with asyncio.TaskGroup() as tg:
                tg.create_task(collect())
                await asyncio.sleep(0.05)
                event = AgentEvent(type="model_call_end", data={"usage": {"total_tokens": 10}})
                await stream.publish(event)
                await asyncio.sleep(0.05)

            assert len(received) == 1
            assert received[0].startswith("data: ")
            assert received[0].endswith("\n\n")

        asyncio.run(run())


# ---------------------------------------------------------------------------
# TuiDashboard
# ---------------------------------------------------------------------------

class TestTuiDashboard:
    """Verify TuiDashboard exists (part of observability)."""

    def test_tui_dashboard_exists(self):
        """TuiDashboard is available in arf/observability/tui.py."""
        from arf.observability.tui import TuiDashboard
        assert TuiDashboard is not None

    def test_tui_dashboard_exported(self):
        """TuiDashboard is listed in __all__ of arf/observability."""
        from arf.observability import TuiDashboard
        assert TuiDashboard is not None


# ---------------------------------------------------------------------------
# Cross-module file existence
# ---------------------------------------------------------------------------

class TestTraceModuleFiles:
    """Doc references specific files — verify they exist."""

    def test_observability_files_exist(self):
        """Doc references these specific files — verify each exists."""
        root = Path(__file__).parent.parent.parent
        files = [
            "arf/observability/file_trace.py",
            "arf/observability/usage_tracker.py",
            "arf/observability/replay.py",
            "arf/observability/otel.py",
            "arf/observability/tui.py",
            "arf/observability/__init__.py",
            "arf/streaming/adapters/sse.py",
            "arf/core/events.py",
            "arf/core/protocols/event_bus.py",
            "arf/core/protocols/tracer.py",
            "arf/core/protocols/replay.py",
        ]
        for f in files:
            assert (root / f).exists(), f"File '{f}' referenced in docs does not exist"

    def test_trace_viewer_html_file_exists(self):
        """Doc 2.7: trace_viewer.html in arf/observability/."""
        viewer_path = Path(__file__).parent.parent.parent / "arf" / "observability" / "trace_viewer.html"
        assert viewer_path.exists()


# ---------------------------------------------------------------------------
# Observations and edge cases
# ---------------------------------------------------------------------------

class TestEventValidation:
    """Additional property checks from the doc."""

    def test_emit_guards_against_null_event_bus(self):
        """Doc: _emit checks `if self.event_bus:` before emitting."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine._emit)
        assert "if self.event_bus:" in src

    def test_make_event_guards_against_null_event_bus(self):
        """Doc: _make_event checks `if self.event_bus:` before emitting."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine._make_event)
        assert "if self.event_bus:" in src

    def test_file_trace_store_records_all_fields_in_json(self):
        """Doc: persisted JSON includes type, data, turn, timestamp, trace_id,
        span_id."""
        import asyncio, tempfile
        from arf.observability.file_trace import FileTraceStore
        from arf.core.events import AgentEvent

        async def run():
            with tempfile.TemporaryDirectory() as d:
                store = FileTraceStore.__new__(FileTraceStore)
                store._dir = Path(d)
                event = AgentEvent(
                    type="tool_call_end", data={"tool_name": "search"},
                    turn=1, timestamp=123.0, trace_id="t1", span_id="s1",
                    session_id="sess1"
                )
                store._append("sess1", event)
                data = store.load("sess1")
                assert len(data) == 1
                record = data[0]
                assert record["type"] == "tool_call_end"
                assert record["data"] == {"tool_name": "search"}
                assert record["turn"] == 1
                assert record["timestamp"] == 123.0
                assert record["trace_id"] == "t1"
                assert record["span_id"] == "s1"

        asyncio.run(run())

    def test_app_context_has_trace_dir_property(self):
        """Doc: AppContext has trace_dir property pointing to ./memory/traces."""
        from arf.agent import AppContext
        from pathlib import Path
        ctx = AppContext(root=Path("/app"))
        assert hasattr(ctx, "trace_dir")
        assert str(ctx.trace_dir) == "/app/memory/traces"
