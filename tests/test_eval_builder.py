"""Unit tests for BenchmarkBuilder."""
import json
import tempfile
from pathlib import Path

import pytest

from arf.plugins.eval.builder import BenchmarkBuilder
from arf.plugins.eval.exceptions import EvalError


class _SimpleTraceReader:
    """Minimal trace file reader — replaces TracePlugin dependency."""

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir

    def read_trace(self, session_id: str) -> list[dict]:
        trace_file = self._data_dir / session_id / "traces" / f"{session_id}.jsonl"
        if not trace_file.exists():
            return []
        events: list[dict] = []
        with open(trace_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events


def _make_trace_reader(data_dir):
    return _SimpleTraceReader(data_dir)


def _write_trace_events(data_dir, session_id, events):
    trace_dir = data_dir / session_id / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_file = trace_dir / f"{session_id}.jsonl"
    with open(trace_file, "a", encoding="utf-8") as f:
        for e in events:
            e.setdefault("session_id", session_id)
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


class TestBenchmarkBuilder:
    @pytest.fixture
    def data_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_build_creates_cases_from_user_inputs(self, data_dir):
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
            {"type": "user_input", "turn": 1,
             "data": {"content": "create file"}, "timestamp": 1.0},
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "file_writer"}, "timestamp": 1.1},
            {"type": "tool_call_end", "turn": 1,
             "data": {"tool_name": "file_writer", "success": True,
                      "result": "created"}, "timestamp": 1.2},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "File created successfully",
                      "tool_calls": [{"name": "file_writer", "params": {}}]},
             "timestamp": 1.3},
            {"type": "user_input", "turn": 3,
             "data": {"content": "read it"}, "timestamp": 2.0},
            {"type": "tool_call_start", "turn": 3,
             "data": {"tool_name": "file_reader"}, "timestamp": 2.1},
            {"type": "tool_call_end", "turn": 3,
             "data": {"tool_name": "file_reader", "success": True,
                      "result": "hello"}, "timestamp": 2.2},
            {"type": "model_call_end", "turn": 3,
             "data": {"content": "The file says hello",
                      "tool_calls": [{"name": "file_reader", "params": {}}]},
             "timestamp": 2.3},
        ])
        builder = BenchmarkBuilder(p)
        bm = builder.build("s1", "my_bench")

        assert bm.name == "my_bench"
        assert bm.source_session == "s1"
        assert len(bm.cases) == 2

        # Case 0
        assert bm.cases[0].input == "create file"
        assert bm.cases[0].expected_execution == ["file_writer"]
        assert bm.cases[0].expected_output_contains == []
        assert bm.cases[0].max_turns == 1
        assert bm.cases[0].source_round == 0

        # Case 1
        assert bm.cases[1].input == "read it"
        assert bm.cases[1].expected_execution == ["file_reader"]

        assert bm.created_at > 0

    def test_build_session_not_found(self, data_dir):
        p = _make_trace_reader(data_dir)
        builder = BenchmarkBuilder(p)
        with pytest.raises(EvalError, match="not found"):
            builder.build("nope", "bm")

    def test_build_no_user_inputs(self, data_dir):
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "x"}, "timestamp": 1.0},
        ])
        builder = BenchmarkBuilder(p)
        with pytest.raises(EvalError, match="No user messages"):
            builder.build("s1", "bm")

    def test_golden_trajectory_no_tool_calls(self, data_dir):
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
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
        assert c.expected_execution == []
        assert c.expected_output_contains == []
        assert c.max_turns == 1

    def test_multi_turn_golden_trajectory(self, data_dir):
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
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
        c = bm.cases[0]
        assert c.expected_output_contains == []
        assert c.max_turns == 2
        assert len(c.expected_execution) == 2
        assert c.expected_execution[0] == "read"
        assert c.expected_execution[1] == "glob"

    def test_annotate_mode_placeholders(self, data_dir):
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
            {"type": "user_input", "turn": 1,
             "data": {"content": "hello"}, "timestamp": 1.0},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "Hi!"}, "timestamp": 1.1},
        ])
        builder = BenchmarkBuilder(p)
        bm = builder.build("s1", "annot", annotate_mode=True)
        c = bm.cases[0]
        assert "[待标注]" in c.expected_output_contains[0]
        assert c.expected_execution == ["[待标注] 预期工具名"]

    def test_feedback_extraction(self, data_dir):
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
            {"type": "user_input", "turn": 1,
             "data": {"content": "write file"}, "timestamp": 1.0},
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "write"}, "timestamp": 1.1},
            {"type": "tool_call_end", "turn": 1,
             "data": {"tool_name": "write", "success": True,
                      "result": "done"}, "timestamp": 1.2},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "Done!",
                      "tool_calls": [{"name": "write", "params": {}}]},
             "timestamp": 1.3},
            {"type": "user_annotation",
             "data": {"feedback": "good", "reason": "works",
                      "annotated_at": "2025-01-01T00:00:00",
                      "round": 0},
             "timestamp": 2.0},
        ])
        builder = BenchmarkBuilder(p)
        bm = builder.build("s1", "fb_test")
        c = bm.cases[0]
        assert c.feedback == {"rating": "good", "reason": "works",
                              "annotated_at": "2025-01-01T00:00:00"}
        assert c.source_round == 0

    def test_feedback_latest_wins(self, data_dir):
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
            {"type": "user_input", "turn": 1,
             "data": {"content": "write file"}, "timestamp": 1.0},
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "write"}, "timestamp": 1.1},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "Done!",
                      "tool_calls": [{"name": "write", "params": {}}]},
             "timestamp": 1.2},
            {"type": "tool_call_end", "turn": 1,
             "data": {"tool_name": "write", "success": True,
                      "result": "done"}, "timestamp": 1.3},
            {"type": "user_annotation",
             "data": {"feedback": "bad", "reason": "broken",
                      "round": 0},
             "timestamp": 2.0},
            {"type": "user_annotation",
             "data": {"feedback": "good", "reason": "fixed",
                      "round": 0},
             "timestamp": 3.0},
        ])
        builder = BenchmarkBuilder(p)
        bm = builder.build("s1", "latest")
        c = bm.cases[0]
        assert c.feedback == {"rating": "good", "reason": "fixed", "annotated_at": ""}


class TestBuildFromAnnotations:
    @pytest.fixture
    def data_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_only_annotated_rounds_become_cases(self, data_dir):
        """build_from_annotations should only extract rounds with user_annotation."""
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
            # Round 0 — annotated
            {"type": "user_input", "turn": 1,
             "data": {"content": "create file"}, "timestamp": 1.0},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "done"}, "timestamp": 1.1},
            {"type": "user_annotation", "round": 0, "timestamp": 1.2,
             "data": {"rating": "like", "comment": "good"}},
            # Round 1 — NOT annotated
            {"type": "user_input", "turn": 2,
             "data": {"content": "read file"}, "timestamp": 2.0},
            {"type": "model_call_end", "turn": 2,
             "data": {"content": "content here"}, "timestamp": 2.1},
            # Round 2 — annotated
            {"type": "user_input", "turn": 3,
             "data": {"content": "delete file"}, "timestamp": 3.0},
            {"type": "model_call_end", "turn": 3,
             "data": {"content": "deleted"}, "timestamp": 3.1},
            {"type": "user_annotation", "round": 2, "timestamp": 3.2,
             "data": {"rating": "dislike", "comment": "wrong"}},
        ])

        builder = BenchmarkBuilder(p)
        bm = builder.build_from_annotations("s1", "test_bm",
                                            benchmark_dir=str(data_dir / "benchmarks"))

        # Only rounds 0 and 2 should become cases
        assert len(bm.cases) == 2
        assert bm.cases[0].input == "create file"
        assert bm.cases[0].source_round == 0
        assert bm.cases[0].feedback == {
            "rating": "like",
            "comment": "good",
            "annotated_at": "",
        }
        assert bm.cases[0].expected_execution == []
        assert bm.cases[0].expected_output_contains == []

        assert bm.cases[1].input == "delete file"
        assert bm.cases[1].source_round == 2
        assert bm.cases[1].feedback["rating"] == "dislike"

    def test_no_annotations_returns_empty(self, data_dir):
        """build_from_annotations with no annotations should return empty benchmark."""
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
            {"type": "user_input", "turn": 1,
             "data": {"content": "hello"}, "timestamp": 1.0},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "hi"}, "timestamp": 1.1},
        ])

        builder = BenchmarkBuilder(p)
        bm = builder.build_from_annotations("s1", "empty_bm",
                                            benchmark_dir=str(data_dir / "benchmarks"))
        assert len(bm.cases) == 0

    def test_feedback_from_latest_annotation(self, data_dir):
        """Multiple annotations on same round: use the latest one."""
        p = _make_trace_reader(data_dir)
        _write_trace_events(data_dir, "s1", [
            {"type": "user_input", "turn": 1,
             "data": {"content": "test"}, "timestamp": 1.0},
            {"type": "model_call_end", "turn": 1,
             "data": {"content": "result"}, "timestamp": 1.1},
            {"type": "user_annotation", "round": 0, "timestamp": 1.2,
             "data": {"rating": "dislike", "comment": "first impression"}},
            {"type": "user_annotation", "round": 0, "timestamp": 2.0,
             "data": {"rating": "like", "comment": "revised opinion"}},
        ])

        builder = BenchmarkBuilder(p)
        bm = builder.build_from_annotations("s1", "test_bm",
                                            benchmark_dir=str(data_dir / "benchmarks"))
        assert len(bm.cases) == 1
        assert bm.cases[0].feedback["rating"] == "like"
        assert bm.cases[0].feedback["comment"] == "revised opinion"
