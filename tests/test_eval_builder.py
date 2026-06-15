"""Unit tests for BenchmarkBuilder."""
import tempfile
from pathlib import Path

import pytest

from arf.plugins.eval.builder import BenchmarkBuilder
from arf.plugins.eval.exceptions import EvalError
from arf.plugins.trace.plugin import TracePlugin


def _make_trace_plugin(data_dir):
    return TracePlugin({"data_dir": str(data_dir), "enabled": True})


def _write_trace_events(p, session_id, events):
    for e in events:
        e.setdefault("session_id", session_id)
        p._write_event(session_id, e)


class TestBenchmarkBuilder:
    @pytest.fixture
    def data_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_build_creates_cases_from_user_inputs(self, data_dir):
        p = _make_trace_plugin(data_dir)
        _write_trace_events(p, "s1", [
            {"type": "user_input", "turn": 1,
             "data": {"content": "create file"}, "timestamp": 1.0},
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "file_writer"}, "timestamp": 1.1},
            {"type": "tool_call_end", "turn": 1,
             "data": {"tool_name": "file_writer", "success": True,
                      "result": "created"}, "timestamp": 1.2},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "File created successfully"},
             "timestamp": 1.3},
            {"type": "user_input", "turn": 3,
             "data": {"content": "read it"}, "timestamp": 2.0},
            {"type": "tool_call_start", "turn": 3,
             "data": {"tool_name": "file_reader"}, "timestamp": 2.1},
            {"type": "tool_call_end", "turn": 3,
             "data": {"tool_name": "file_reader", "success": True,
                      "result": "hello"}, "timestamp": 2.2},
            {"type": "model_call_end", "turn": 3,
             "data": {"content": "The file says hello"},
             "timestamp": 2.3},
        ])
        builder = BenchmarkBuilder(p)
        bm = builder.build("s1", "my_bench")

        assert bm.name == "my_bench"
        assert bm.source_session == "s1"
        assert len(bm.cases) == 2

        # Case 0
        assert bm.cases[0].input == "create file"
        assert bm.cases[0].expected_tools == ["file_writer"]
        assert bm.cases[0].golden_trajectory is not None
        assert len(bm.cases[0].golden_trajectory["turns"]) >= 1
        t0 = bm.cases[0].golden_trajectory["turns"][0]
        assert t0["assistant"]["content"] == "File created successfully"
        assert len(t0["tool_results"]) == 1
        assert t0["tool_results"][0]["tool_name"] == "file_writer"
        assert "File created" in t0["assistant_final"]["content"]

        # Case 1
        assert bm.cases[1].input == "read it"
        assert bm.cases[1].expected_tools == ["file_reader"]

        assert bm.created_at > 0

    def test_build_session_not_found(self, data_dir):
        p = _make_trace_plugin(data_dir)
        builder = BenchmarkBuilder(p)
        with pytest.raises(EvalError, match="not found"):
            builder.build("nope", "bm")

    def test_build_no_user_inputs(self, data_dir):
        p = _make_trace_plugin(data_dir)
        _write_trace_events(p, "s1", [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "x"}, "timestamp": 1.0},
        ])
        builder = BenchmarkBuilder(p)
        with pytest.raises(EvalError, match="No user messages"):
            builder.build("s1", "bm")

    def test_golden_trajectory_no_tool_calls(self, data_dir):
        p = _make_trace_plugin(data_dir)
        _write_trace_events(p, "s1", [
            {"type": "user_input", "turn": 1,
             "data": {"content": "hello"}, "timestamp": 1.0},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "Hi there! How can I help?"},
             "timestamp": 1.1},
        ])
        builder = BenchmarkBuilder(p)
        bm = builder.build("s1", "chat")
        assert len(bm.cases) == 1
        c = bm.cases[0]
        assert c.expected_tools is None
        assert c.golden_trajectory is not None
        t0 = c.golden_trajectory["turns"][0]
        assert t0["assistant"]["content"] == "Hi there! How can I help?"
        assert t0["assistant"]["tool_calls"] == []

    def test_multi_turn_golden_trajectory(self, data_dir):
        p = _make_trace_plugin(data_dir)
        _write_trace_events(p, "s1", [
            {"type": "user_input", "turn": 1,
             "data": {"content": "read x"}, "timestamp": 1.0},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "", "tool_calls": [
                 {"name": "read", "params": {"path": "x"}}
             ]}, "timestamp": 1.1},
            {"type": "tool_call_end", "turn": 1,
             "data": {"tool_name": "read", "result": "not found",
                      "success": False}, "timestamp": 1.2},
            {"type": "model_call_end", "turn": 2,
             "data": {"content": "", "tool_calls": [
                 {"name": "glob", "params": {"pattern": "*.txt"}}
             ]}, "timestamp": 2.1},
            {"type": "tool_call_end", "turn": 2,
             "data": {"tool_name": "glob", "result": "x.txt",
                      "success": True}, "timestamp": 2.2},
            {"type": "model_call_end", "turn": 2,
             "data": {"content": "Found x.txt via glob"},
             "timestamp": 2.3},
        ])
        builder = BenchmarkBuilder(p)
        bm = builder.build("s1", "multi")
        assert len(bm.cases) == 1
        gt = bm.cases[0].golden_trajectory
        assert gt is not None
        assert len(gt["turns"]) == 2
        assert gt["turns"][0]["turn"] == 1
        assert gt["turns"][0]["tool_results"][0]["success"] is False
        assert gt["turns"][1]["turn"] == 2
        assert gt["turns"][1]["tool_results"][0]["success"] is True
