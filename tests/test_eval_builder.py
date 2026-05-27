"""Unit tests for BenchmarkBuilder."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from arf.evaluation.builder import BenchmarkBuilder
from arf.evaluation.exceptions import EvalError
from arf.observability.file_trace import FileTraceStore
from arf.event_bus import InMemoryEventBus
from arf.core.events import AgentEvent


def _make_store(trace_dir):
    """Create FileTraceStore within an async context."""
    async def _make():
        return FileTraceStore(InMemoryEventBus(), dir=trace_dir)
    return asyncio.run(_make())


def _write_trace(dir, session_id, events):
    p = Path(dir) / f"{session_id}.json"
    p.write_text(json.dumps([
        {"type": e.type, "data": e.data, "turn": e.turn, "timestamp": e.timestamp}
        for e in events
    ]))


class TestBenchmarkBuilder:
    @pytest.fixture
    def trace_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_build_creates_cases_from_user_inputs(self, trace_dir):
        _write_trace(trace_dir, "s1", [
            AgentEvent(type="user_input", turn=1, data={"content": "create file"}),
            AgentEvent(type="tool_call_start", turn=1, data={"tool_name": "file_writer"}),
            AgentEvent(type="tool_call_end", turn=1, data={"tool_name": "file_writer",
                          "success": True}),
            AgentEvent(type="model_call_end", turn=1, data={"content": "done"}),
            AgentEvent(type="user_input", turn=3, data={"content": "read it"}),
            AgentEvent(type="tool_call_start", turn=3, data={"tool_name": "file_reader"}),
            AgentEvent(type="tool_call_end", turn=3, data={"tool_name": "file_reader",
                          "success": True}),
        ])
        store = _make_store(trace_dir)
        builder = BenchmarkBuilder(store)
        bm = builder.build("s1", "my_bench")

        assert bm.name == "my_bench"
        assert bm.source_session == "s1"
        assert len(bm.cases) == 2
        assert bm.cases[0].input == "create file"
        assert bm.cases[0].expected_tools == ["file_writer"]
        assert bm.cases[1].input == "read it"
        assert bm.cases[1].expected_tools == ["file_reader"]
        assert bm.created_at > 0

    def test_build_session_not_found(self, trace_dir):
        store = _make_store(trace_dir)
        builder = BenchmarkBuilder(store)
        with pytest.raises(EvalError, match="not found"):
            builder.build("nope", "bm")

    def test_build_no_user_inputs(self, trace_dir):
        _write_trace(trace_dir, "s1", [
            AgentEvent(type="tool_call_start", turn=1, data={"tool_name": "x"}),
        ])
        store = _make_store(trace_dir)
        builder = BenchmarkBuilder(store)
        with pytest.raises(EvalError, match="No user messages"):
            builder.build("s1", "bm")
