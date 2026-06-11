"""Fact-check tests: Eval/Benchmark — docs/eval-benchmark.md vs arf/plugins/eval/.

Each test validates a specific claim made in the documentation against actual code.
PASS = doc/code consistent. FAIL = discrepancy found (fact-check finding).
"""

import inspect
import json
import time
import asyncio
from pathlib import Path
from dataclasses import fields

import pytest

from arf.event_bus import InMemoryEventBus


class _EventsStore:
    """Minimal trace store for BenchmarkBuilder tests."""
    def __init__(self, events):
        self._events = events
    def load(self, session_id):
        return list(self._events)
    def read_trace(self, session_id):
        """TracePlugin-compatible read_trace — alias to load."""
        return list(self._events)


class _FakeEvalAgent:
    """Minimal agent for EvalRunner tests."""
    def __init__(self, chat_response="response text", config="test_config"):
        self._chat_response = chat_response
        self.config = config

    async def chat(self, input_text, session_id=""):
        if isinstance(self._chat_response, BaseException):
            raise self._chat_response
        return self._chat_response


# ---------------------------------------------------------------------------
# 1. Top-level imports (docs 3.1, 3.2, 3.3)
# ---------------------------------------------------------------------------

class TestTopLevelImports:
    """Doc Section 3 API Reference: all imports from arf.plugins.eval."""

    def test_import_benchmark_builder(self):
        """Doc: from arf.plugins.eval import BenchmarkBuilder."""
        from arf.plugins.eval import BenchmarkBuilder
        assert BenchmarkBuilder is not None

    def test_import_eval_benchmark(self):
        """Doc: from arf.plugins.eval import EvalBenchmark."""
        from arf.plugins.eval import EvalBenchmark
        assert EvalBenchmark is not None

    def test_import_eval_runner(self):
        """Doc: from arf.plugins.eval import EvalRunner."""
        from arf.plugins.eval import EvalRunner
        assert EvalRunner is not None

    def test_import_eval_comparator(self):
        """Doc: from arf.plugins.eval import EvalComparator."""
        from arf.plugins.eval import EvalComparator
        assert EvalComparator is not None

    def test_import_eval_report(self):
        """Doc: from arf.plugins.eval import EvalReport."""
        from arf.plugins.eval import EvalReport
        assert EvalReport is not None

    def test_import_eval_diff(self):
        """Doc: from arf.plugins.eval import EvalDiff (via EvalComparator)."""
        from arf.plugins.eval import EvalDiff
        assert EvalDiff is not None

    def test_import_eval_summary(self):
        """Doc: from arf.plugins.eval import EvalSummary."""
        from arf.plugins.eval import EvalSummary
        assert EvalSummary is not None

    def test_import_eval_case(self):
        """Doc: from arf.plugins.eval import EvalCase."""
        from arf.plugins.eval import EvalCase
        assert EvalCase is not None

    def test_import_trace_plugin(self):
        """REMOVED 2026-06-11: FileTraceStore deleted. TracePlugin handles trace persistence."""
        from arf.plugins.trace.plugin import TracePlugin
        assert TracePlugin is not None

    def test_import_metrics(self):
        """Doc: from arf.plugins.eval import (metrics)."""
        from arf.plugins.eval import (
            SuccessRateMetric, ToolAccuracyMetric,
            TurnEfficiencyMetric, OutputContainsMetric,
        )
        assert SuccessRateMetric is not None
        assert ToolAccuracyMetric is not None
        assert TurnEfficiencyMetric is not None
        assert OutputContainsMetric is not None

    def test_import_eval_error(self):
        """Doc: from arf.plugins.eval.exceptions import EvalError."""
        from arf.plugins.eval.exceptions import EvalError
        assert EvalError is not None

    def test_import_events_to_trace(self):
        """Doc: from arf.plugins.eval.trace_adapter import events_to_trace."""
        from arf.plugins.eval.trace_adapter import events_to_trace
        assert events_to_trace is not None


# ---------------------------------------------------------------------------
# 2. Module / file existence (docs passim)
# ---------------------------------------------------------------------------

class TestFileExistence:
    """Doc references these specific files in arf/plugins/eval/."""

    def test_evaluation_files_exist(self):
        """Doc: arf/plugins/eval/ directory with runner, builder, comparator, etc."""
        root = Path(__file__).parent.parent.parent
        for f in (
            "arf/plugins/eval/__init__.py",
            "arf/plugins/eval/runner.py",
            "arf/plugins/eval/builder.py",
            "arf/plugins/eval/comparator.py",
            "arf/plugins/eval/metrics.py",
            "arf/plugins/eval/models.py",
            "arf/plugins/eval/exceptions.py",
            "arf/plugins/eval/trace_adapter.py",
        ):
            assert (root / f).exists(), f"Expected file {f} does not exist"

    def test_protocol_file_exists(self):
        """Doc: arf/core/protocols/evaluation.py defines protocols."""
        root = Path(__file__).parent.parent.parent
        assert (root / "arf/core/protocols/evaluation.py").exists()


# ---------------------------------------------------------------------------
# 3. EvalCase data model (docs Section 4)
# ---------------------------------------------------------------------------

class TestEvalCaseModel:
    """Doc Section 4: EvalCase fields."""

    def test_eval_case_fields(self):
        """Doc: id: str, input: str, expected_tools, expected_output_contains, max_turns."""
        from arf.plugins.eval.models import EvalCase
        field_names = {f.name for f in fields(EvalCase)}
        assert "id" in field_names
        assert "input" in field_names
        assert "expected_tools" in field_names
        assert "expected_output_contains" in field_names
        assert "max_turns" in field_names

    def test_eval_case_types(self):
        """Doc: expected_tools = list[str] | None, max_turns = int | None."""
        from arf.plugins.eval.models import EvalCase
        fmap = {f.name: f.type for f in fields(EvalCase)}
        assert fmap["id"] is str or fmap["id"] == "str"
        assert fmap["input"] is str or fmap["input"] == "str"
        expected_tools_type = fmap.get("expected_tools")
        assert expected_tools_type is not None
        max_turns_type = fmap.get("max_turns")
        assert max_turns_type is not None

    def test_eval_case_construct(self):
        """Doc: EvalCase(id='case_0', input='hello')."""
        from arf.plugins.eval.models import EvalCase
        c = EvalCase(id="case_0", input="hello")
        assert c.id == "case_0"
        assert c.input == "hello"
        assert c.expected_tools is None
        assert c.expected_output_contains is None
        assert c.max_turns is None

    def test_eval_case_with_all_fields(self):
        """Doc: All fields can be set."""
        from arf.plugins.eval.models import EvalCase
        c = EvalCase(
            id="case_1", input="test",
            expected_tools=["read_file"],
            expected_output_contains=["success"],
            max_turns=5,
        )
        assert c.expected_tools == ["read_file"]
        assert c.expected_output_contains == ["success"]
        assert c.max_turns == 5


# ---------------------------------------------------------------------------
# 4. EvalBenchmark data model (docs Section 4)
# ---------------------------------------------------------------------------

class TestEvalBenchmarkModel:
    """Doc Section 4: EvalBenchmark fields."""

    def test_eval_benchmark_fields(self):
        """Doc: name, source_session, created_at, cases."""
        from arf.plugins.eval.models import EvalBenchmark
        field_names = {f.name for f in fields(EvalBenchmark)}
        assert "name" in field_names
        assert "source_session" in field_names
        assert "created_at" in field_names
        assert "cases" in field_names

    def test_eval_benchmark_construct(self):
        """Doc: EvalBenchmark(name=..., source_session=..., created_at=..., cases=...)."""
        from arf.plugins.eval.models import EvalCase, EvalBenchmark
        bm = EvalBenchmark(
            name="file_ops_v1",
            source_session="default",
            created_at=1234567890.0,
            cases=[EvalCase(id="case_0", input="hello")],
        )
        assert bm.name == "file_ops_v1"
        assert bm.source_session == "default"
        assert bm.created_at == 1234567890.0
        assert len(bm.cases) == 1
        assert bm.cases[0].input == "hello"

    def test_to_json_round_trip(self, tmp_path):
        """Doc: benchmark.to_json(path) and EvalBenchmark.from_json(path)."""
        from arf.plugins.eval.models import EvalCase, EvalBenchmark
        bm = EvalBenchmark(
            name="test_bm",
            source_session="sess_1",
            created_at=100.0,
            cases=[
                EvalCase(id="case_0", input="hello", expected_tools=["tool_a"]),
                EvalCase(id="case_1", input="world", expected_output_contains=["ok"]),
            ],
        )
        p = str(tmp_path / "benchmark.json")
        bm.to_json(p)
        assert Path(p).exists()

        loaded = EvalBenchmark.from_json(p)
        assert loaded.name == "test_bm"
        assert loaded.source_session == "sess_1"
        assert loaded.created_at == 100.0
        assert len(loaded.cases) == 2
        assert loaded.cases[0].id == "case_0"
        assert loaded.cases[0].input == "hello"
        assert loaded.cases[0].expected_tools == ["tool_a"]
        assert loaded.cases[1].expected_output_contains == ["ok"]

    def test_from_json_missing_optional_fields(self, tmp_path):
        """Doc: from_json handles missing optional fields gracefully."""
        from arf.plugins.eval.models import EvalBenchmark
        data = {
            "name": "minimal",
            "cases": [{"id": "c0", "input": "hi"}],
        }
        p = str(tmp_path / "minimal.json")
        with open(p, "w") as f:
            json.dump(data, f)
        loaded = EvalBenchmark.from_json(p)
        assert loaded.name == "minimal"
        assert loaded.source_session is None
        assert len(loaded.cases) == 1
        assert loaded.cases[0].expected_tools is None

    def test_to_json_skips_none_fields(self, tmp_path):
        """Doc: to_json omits None optional fields."""
        from arf.plugins.eval.models import EvalCase, EvalBenchmark
        bm = EvalBenchmark(
            name="no_optionals",
            cases=[EvalCase(id="c0", input="hi")],
        )
        p = str(tmp_path / "no_opt.json")
        bm.to_json(p)
        with open(p) as f:
            raw = json.load(f)
        assert "expected_tools" not in raw["cases"][0]
        assert "expected_output_contains" not in raw["cases"][0]
        assert "max_turns" not in raw["cases"][0]


# ---------------------------------------------------------------------------
# 5. EvalReport data model (docs Section 4)
# ---------------------------------------------------------------------------

class TestEvalReportModel:
    """Doc Section 4: EvalReport fields."""

    def test_eval_report_fields(self):
        """Doc: run_id, benchmark_name, agent_config_hash, timestamp, summary, per_case."""
        from arf.plugins.eval.models import EvalReport
        field_names = {f.name for f in fields(EvalReport)}
        assert "run_id" in field_names
        assert "benchmark_name" in field_names
        assert "agent_config_hash" in field_names
        assert "timestamp" in field_names
        assert "summary" in field_names
        assert "per_case" in field_names

    def test_to_json_round_trip(self, tmp_path):
        """Doc: report.to_json(path) and EvalReport.from_json(path)."""
        from arf.plugins.eval.models import EvalReport, EvalSummary
        report = EvalReport(
            run_id="uuid-123",
            benchmark_name="test_bm",
            agent_config_hash="abc123",
            timestamp=200.0,
            summary=EvalSummary(total=2, passed=2, failed=0, pass_rate=1.0),
            per_case=[{"case_id": "c0", "passed": True}],
        )
        p = str(tmp_path / "report.json")
        report.to_json(p)
        assert Path(p).exists()

        loaded = EvalReport.from_json(p)
        assert loaded.run_id == "uuid-123"
        assert loaded.benchmark_name == "test_bm"
        assert loaded.agent_config_hash == "abc123"
        assert loaded.summary.total == 2
        assert loaded.summary.pass_rate == 1.0
        assert loaded.per_case == [{"case_id": "c0", "passed": True}]

    def test_from_json_missing_summary_fields(self, tmp_path):
        """Doc: from_json handles missing summary fields gracefully."""
        from arf.plugins.eval.models import EvalReport
        data = {
            "run_id": "r1",
            "benchmark_name": "bm1",
            "agent_config_hash": "h1",
            "timestamp": 1.0,
            "summary": {"total": 1, "passed": 1, "failed": 0, "pass_rate": 1.0},
        }
        p = str(tmp_path / "min_report.json")
        with open(p, "w") as f:
            json.dump(data, f)
        loaded = EvalReport.from_json(p)
        assert loaded.summary.avg_turns == 0.0
        assert loaded.summary.avg_tool_calls == 0.0
        assert loaded.summary.avg_duration_seconds == 0.0
        assert loaded.summary.tool_accuracy == 0.0
        assert loaded.summary.output_contains == 0.0


# ---------------------------------------------------------------------------
# 6. EvalSummary data model (docs Section 4)
# ---------------------------------------------------------------------------

class TestEvalSummaryModel:
    """Doc Section 4: EvalSummary fields."""

    def test_eval_summary_fields(self):
        """Doc: total, passed, failed, pass_rate, avg_turns, avg_tool_calls,
        avg_duration_seconds, tool_accuracy, output_contains."""
        from arf.plugins.eval.models import EvalSummary
        field_names = {f.name for f in fields(EvalSummary)}
        for f in ("total", "passed", "failed", "pass_rate", "avg_turns",
                  "avg_tool_calls", "avg_duration_seconds", "tool_accuracy",
                  "output_contains"):
            assert f in field_names, f"Field '{f}' missing from EvalSummary"

    def test_eval_summary_defaults(self):
        """Doc: All fields default to 0."""
        from arf.plugins.eval.models import EvalSummary
        s = EvalSummary()
        assert s.total == 0
        assert s.passed == 0
        assert s.failed == 0
        assert s.pass_rate == 0.0


# ---------------------------------------------------------------------------
# 7. EvalDiff data model (docs Section 3.3)
# ---------------------------------------------------------------------------

class TestEvalDiffModel:
    """Doc Section 3.3: EvalDiff has summary_diff, regressions, improvements."""

    def test_eval_diff_fields(self):
        """Doc: diff.summary_diff, diff.regressions, diff.improvements."""
        from arf.plugins.eval.models import EvalDiff
        field_names = {f.name for f in fields(EvalDiff)}
        assert "summary_diff" in field_names
        assert "regressions" in field_names
        assert "improvements" in field_names
        assert "baseline_run_id" in field_names
        assert "current_run_id" in field_names

    def test_eval_diff_defaults(self):
        """Doc: EvalDiff has sensible defaults."""
        from arf.plugins.eval.models import EvalDiff
        d = EvalDiff(baseline_run_id="b1", current_run_id="c1")
        assert d.summary_diff == {}
        assert d.regressions == []
        assert d.improvements == []


# ---------------------------------------------------------------------------
# 8. BenchmarkBuilder (docs Section 3.1)
# ---------------------------------------------------------------------------

class TestBenchmarkBuilder:
    """Doc Section 3.1: BenchmarkBuilder creates EvalBenchmark from traces."""

    def test_constructor_accepts_trace_plugin(self):
        """Doc: BenchmarkBuilder(trace_plugin)."""
        from arf.plugins.eval.builder import BenchmarkBuilder
        sig = inspect.signature(BenchmarkBuilder.__init__)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "trace_plugin" in params

    def test_build_signature(self):
        """Doc: builder.build(session_id='default', name='file_ops_v1') -> EvalBenchmark."""
        from arf.plugins.eval.builder import BenchmarkBuilder
        sig = inspect.signature(BenchmarkBuilder.build)
        params = list(sig.parameters.keys())
        assert "session_id" in params
        assert "name" in params

    def test_build_creates_benchmark(self):
        """Doc: build() returns EvalBenchmark with cases from user messages."""
        from arf.plugins.eval.builder import BenchmarkBuilder
        store = _EventsStore([
            {"type": "user_input", "turn": 0, "data": {"content": "hello"}},
            {"type": "user_input", "turn": 1, "data": {"content": "world"}},
        ])
        builder = BenchmarkBuilder(store)
        bm = builder.build(session_id="default", name="test_bm")
        assert bm.name == "test_bm"
        assert bm.source_session == "default"
        assert bm.created_at > 0
        assert len(bm.cases) == 2
        assert bm.cases[0].id == "case_0"
        assert bm.cases[0].input == "hello"
        assert bm.cases[1].id == "case_1"
        assert bm.cases[1].input == "world"

    def test_build_raises_on_empty_session(self):
        """Doc: Raises EvalError if session not found."""
        from arf.plugins.eval.builder import BenchmarkBuilder
        from arf.plugins.eval.exceptions import EvalError
        store = _EventsStore([])
        builder = BenchmarkBuilder(store)
        with pytest.raises(EvalError, match="not found"):
            builder.build(session_id="nonexistent", name="bm")

    def test_build_raises_on_no_user_messages(self):
        """Doc: Raises EvalError if no user messages found."""
        from arf.plugins.eval.builder import BenchmarkBuilder
        from arf.plugins.eval.exceptions import EvalError
        store = _EventsStore([
            {"type": "tool_call_start", "turn": 0, "data": {"tool_name": "ls"}},
        ])
        builder = BenchmarkBuilder(store)
        with pytest.raises(EvalError, match="No user messages"):
            builder.build(session_id="sess", name="bm")

    def test_build_extracts_tools_from_same_turn(self):
        """Doc: expected_tools extracted from tool_call_start events in same turn."""
        from arf.plugins.eval.builder import BenchmarkBuilder
        store = _EventsStore([
            {"type": "user_input", "turn": 0, "data": {"content": "list files"}},
            {"type": "tool_call_start", "turn": 0, "data": {"tool_name": "list_directory"}},
            {"type": "tool_call_start", "turn": 0, "data": {"tool_name": "read_file"}},
            {"type": "user_input", "turn": 1, "data": {"content": "next"}},
        ])
        builder = BenchmarkBuilder(store)
        bm = builder.build(session_id="default", name="test")
        assert bm.cases[0].expected_tools == ["list_directory", "read_file"]
        assert bm.cases[1].expected_tools is None


# ---------------------------------------------------------------------------
# 9. EvalRunner (docs Section 3.2)
# ---------------------------------------------------------------------------

class TestEvalRunner:
    """Doc Section 3.2: EvalRunner runs benchmarks against agent."""

    def test_constructor_accepts_agent_and_event_bus(self):
        """Doc: EvalRunner(agent, agent.event_bus)."""
        from arf.plugins.eval.runner import EvalRunner
        sig = inspect.signature(EvalRunner.__init__)
        params = list(sig.parameters.keys())
        assert "agent" in params
        assert "event_bus" in params

    def test_run_is_async(self):
        """Doc: await runner.run(benchmark) -> EvalReport."""
        from arf.plugins.eval.runner import EvalRunner
        assert inspect.iscoroutinefunction(EvalRunner.run)

    def test_run_signature(self):
        """2026-05-29: run(benchmark) — max_parallel removed (session isolation not ready)."""
        from arf.plugins.eval.runner import EvalRunner
        sig = inspect.signature(EvalRunner.run)
        params = list(sig.parameters.keys())
        assert "benchmark" in params
        assert "max_parallel" not in params

    def test_run_returns_report(self):
        """Doc: run() returns EvalReport."""
        from arf.plugins.eval.runner import EvalRunner
        from arf.plugins.eval.models import EvalBenchmark, EvalCase
        agent = _FakeEvalAgent(chat_response="response text", config="test_config")
        bus = InMemoryEventBus()

        runner = EvalRunner(agent, bus)
        bm = EvalBenchmark(
            name="test",
            cases=[EvalCase(id="c0", input="hello")],
        )

        async def run():
            report = await runner.run(bm)
            return report

        report = asyncio.run(run())
        assert report.benchmark_name == "test"
        assert report.run_id is not None
        assert report.timestamp > 0
        assert report.summary.total == 1
        assert report.summary.passed == 1
        assert report.agent_config_hash is not None

    def test_run_records_failure(self):
        """Doc: run() records failed cases with error info."""
        from arf.plugins.eval.runner import EvalRunner
        from arf.plugins.eval.models import EvalBenchmark, EvalCase
        agent = _FakeEvalAgent(chat_response=ValueError("agent crashed"), config="test")
        bus = InMemoryEventBus()

        runner = EvalRunner(agent, bus)
        bm = EvalBenchmark(
            name="test",
            cases=[EvalCase(id="c0", input="hello")],
        )

        async def run():
            return await runner.run(bm)

        report = asyncio.run(run())
        assert report.summary.total == 1
        assert report.summary.passed == 0
        assert report.summary.failed == 1
        assert report.per_case[0]["passed"] is False
        assert "agent crashed" in report.per_case[0]["error"]


# ---------------------------------------------------------------------------
# 10. EvalComparator (docs Section 3.3)
# ---------------------------------------------------------------------------

class TestEvalComparator:
    """Doc Section 3.3: EvalComparator diffs two EvalReports."""

    def test_compare_returns_eval_diff(self):
        """Doc: EvalComparator().compare(baseline, current) -> EvalDiff."""
        from arf.plugins.eval.comparator import EvalComparator
        from arf.plugins.eval.models import EvalReport, EvalSummary

        baseline = EvalReport(
            run_id="base1", benchmark_name="bm1",
            agent_config_hash="h1", timestamp=1.0,
            summary=EvalSummary(total=1, passed=1, failed=0, pass_rate=1.0),
        )
        current = EvalReport(
            run_id="cur1", benchmark_name="bm1",
            agent_config_hash="h2", timestamp=2.0,
            summary=EvalSummary(total=1, passed=0, failed=1, pass_rate=0.0),
        )
        diff = EvalComparator().compare(baseline, current)
        assert diff.baseline_run_id == "base1"
        assert diff.current_run_id == "cur1"
        assert diff.summary_diff["pass_rate"] == -1.0

    def test_compare_detects_regressions_and_improvements(self):
        """Doc: diff.regressions and diff.improvements per case."""
        from arf.plugins.eval.comparator import EvalComparator
        from arf.plugins.eval.models import EvalReport, EvalSummary

        baseline = EvalReport(
            run_id="b1", benchmark_name="bm",
            agent_config_hash="h1", timestamp=1.0,
            summary=EvalSummary(),
            per_case=[
                {"case_id": "c0", "metrics": {"tool_accuracy": 1.0, "output_contains": 0.0}},
                {"case_id": "c1", "metrics": {"tool_accuracy": 0.5, "output_contains": 0.5}},
            ],
        )
        current = EvalReport(
            run_id="c1", benchmark_name="bm",
            agent_config_hash="h2", timestamp=2.0,
            summary=EvalSummary(),
            per_case=[
                {"case_id": "c0", "metrics": {"tool_accuracy": 0.3, "output_contains": 0.0}},
                {"case_id": "c1", "metrics": {"tool_accuracy": 0.5, "output_contains": 1.0}},
            ],
        )
        diff = EvalComparator().compare(baseline, current)
        assert len(diff.regressions) == 1
        assert diff.regressions[0]["case_id"] == "c0"
        assert diff.regressions[0]["metric"] == "tool_accuracy"
        assert len(diff.improvements) == 1
        assert diff.improvements[0]["case_id"] == "c1"
        assert diff.improvements[0]["metric"] == "output_contains"

    def test_compare_raises_on_mismatched_benchmarks(self):
        """Doc: Cannot compare different benchmarks."""
        from arf.plugins.eval.comparator import EvalComparator
        from arf.plugins.eval.models import EvalReport, EvalSummary
        from arf.plugins.eval.exceptions import EvalError

        baseline = EvalReport(
            run_id="b1", benchmark_name="bm_a",
            agent_config_hash="h1", timestamp=1.0,
        )
        current = EvalReport(
            run_id="c1", benchmark_name="bm_b",
            agent_config_hash="h2", timestamp=2.0,
        )
        with pytest.raises(EvalError, match="Cannot compare different benchmarks"):
            EvalComparator().compare(baseline, current)

    def test_summary_diff_contains_all_fields(self):
        """Doc: summary_diff includes pass_rate, avg_turns, avg_tool_calls,
        avg_duration_seconds, tool_accuracy, output_contains."""
        from arf.plugins.eval.comparator import EvalComparator
        from arf.plugins.eval.models import EvalReport, EvalSummary
        b = EvalReport(run_id="b", benchmark_name="x", agent_config_hash="h", timestamp=1.0,
                       summary=EvalSummary(pass_rate=0.8, avg_turns=1.0, avg_tool_calls=2.0,
                                           avg_duration_seconds=3.0, tool_accuracy=0.9, output_contains=0.7))
        c = EvalReport(run_id="c", benchmark_name="x", agent_config_hash="h", timestamp=2.0,
                       summary=EvalSummary(pass_rate=1.0, avg_turns=2.0, avg_tool_calls=3.0,
                                           avg_duration_seconds=4.0, tool_accuracy=1.0, output_contains=0.8))
        diff = EvalComparator().compare(b, c)
        assert "pass_rate" in diff.summary_diff
        assert "avg_turns" in diff.summary_diff
        assert "avg_tool_calls" in diff.summary_diff
        assert "avg_duration_seconds" in diff.summary_diff
        assert "tool_accuracy" in diff.summary_diff
        assert "output_contains" in diff.summary_diff


# ---------------------------------------------------------------------------
# 11. Metrics (docs Section 5)
# ---------------------------------------------------------------------------

class TestSuccessRateMetric:
    """Doc Section 5: SuccessRateMetric — no errors → 1.0, errors → 0.0."""

    def test_no_errors_returns_1(self):
        """Doc: trace 所有 turn 中无 error 事件 → 1.0."""
        from arf.plugins.eval.metrics import SuccessRateMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = SuccessRateMetric()
        trace = {"turns": [{"turn": 0, "model_output": "ok"}, {"turn": 1, "model_output": "done"}]}
        expected = EvalCase(id="c0", input="hi")

        async def run():
            return await metric.compute(trace, expected)

        result = asyncio.run(run())
        assert result == {"success_rate": 1.0}

    def test_with_errors_returns_0(self):
        """Doc: trace 有 error 事件 → 0.0."""
        from arf.plugins.eval.metrics import SuccessRateMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = SuccessRateMetric()
        trace = {"turns": [
            {"turn": 0, "model_output": "ok"},
            {"turn": 1, "error": "timeout", "model_output": ""},
        ]}
        expected = EvalCase(id="c0", input="hi")

        async def run():
            return await metric.compute(trace, expected)

        result = asyncio.run(run())
        assert result == {"success_rate": 0.0}

    def test_empty_trace_returns_1(self):
        """Doc: Empty trace has no errors → 1.0."""
        from arf.plugins.eval.metrics import SuccessRateMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = SuccessRateMetric()
        trace = {"turns": []}

        async def run():
            return await metric.compute(trace, EvalCase(id="c0", input="hi"))

        result = asyncio.run(run())
        assert result == {"success_rate": 1.0}


class TestToolAccuracyMetric:
    """Doc Section 5: ToolAccuracyMetric — ordered matching."""

    def test_exact_match(self):
        """Doc: 匹配数 / len(expected_tools)."""
        from arf.plugins.eval.metrics import ToolAccuracyMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = ToolAccuracyMetric()
        trace = {"turns": [{"tool_calls": [{"tool_name": "ls"}, {"tool_name": "cat"}]}]}
        expected = EvalCase(id="c0", input="hi", expected_tools=["ls", "cat"])

        async def run():
            return await metric.compute(trace, expected)

        result = asyncio.run(run())
        assert result["tool_accuracy"] == 1.0

    def test_partial_match(self):
        """Doc: sequence-ordered partial match."""
        from arf.plugins.eval.metrics import ToolAccuracyMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = ToolAccuracyMetric()
        trace = {"turns": [{"tool_calls": [{"tool_name": "ls"}, {"tool_name": "rm"}]}]}
        expected = EvalCase(id="c0", input="hi", expected_tools=["ls", "cat"])

        async def run():
            return await metric.compute(trace, expected)

        result = asyncio.run(run())
        assert result["tool_accuracy"] == 0.5

    def test_no_expected_tools_returns_1(self):
        """Doc: expected_tools None → 1.0."""
        from arf.plugins.eval.metrics import ToolAccuracyMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = ToolAccuracyMetric()
        trace = {"turns": [{"tool_calls": [{"tool_name": "ls"}]}]}

        async def run():
            return await metric.compute(trace, EvalCase(id="c0", input="hi"))

        result = asyncio.run(run())
        assert result["tool_accuracy"] == 1.0

    def test_no_actual_calls_returns_0(self):
        """Doc: No actual tool calls → 0.0 when tools expected."""
        from arf.plugins.eval.metrics import ToolAccuracyMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = ToolAccuracyMetric()
        trace = {"turns": [{"tool_calls": []}]}

        async def run():
            return await metric.compute(trace, EvalCase(id="c0", input="hi", expected_tools=["ls"]))

        result = asyncio.run(run())
        assert result["tool_accuracy"] == 0.0


class TestTurnEfficiencyMetric:
    """Doc Section 5: TurnEfficiencyMetric — returns turn_count."""

    def test_returns_turn_count(self):
        """Doc: Returns trace turn count."""
        from arf.plugins.eval.metrics import TurnEfficiencyMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = TurnEfficiencyMetric()
        trace = {"turns": [{"turn": 0}, {"turn": 1}, {"turn": 2}]}

        async def run():
            return await metric.compute(trace, EvalCase(id="c0", input="hi"))

        result = asyncio.run(run())
        assert result == {"turn_count": 3.0}

    def test_empty_trace_returns_0(self):
        """Doc: Empty trace returns 0."""
        from arf.plugins.eval.metrics import TurnEfficiencyMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = TurnEfficiencyMetric()
        trace = {"turns": []}

        async def run():
            return await metric.compute(trace, EvalCase(id="c0", input="hi"))

        result = asyncio.run(run())
        assert result == {"turn_count": 0.0}


class TestOutputContainsMetric:
    """Doc Section 5: OutputContainsMetric — keywords in last model_output."""

    def test_all_keywords_present(self):
        """Doc: All keywords found in last model_output."""
        from arf.plugins.eval.metrics import OutputContainsMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = OutputContainsMetric()
        trace = {"turns": [
            {"turn": 0, "model_output": "first response"},
            {"turn": 1, "model_output": "Operation completed successfully"},
        ]}
        expected = EvalCase(id="c0", input="hi", expected_output_contains=["completed", "successfully"])

        async def run():
            return await metric.compute(trace, expected)

        result = asyncio.run(run())
        assert result["output_contains"] == 1.0

    def test_partial_keywords(self):
        """Doc: Partial keyword match returns proportion."""
        from arf.plugins.eval.metrics import OutputContainsMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = OutputContainsMetric()
        trace = {"turns": [
            {"turn": 0, "model_output": "Operation completed"},
        ]}
        expected = EvalCase(id="c0", input="hi", expected_output_contains=["completed", "failed"])

        async def run():
            return await metric.compute(trace, expected)

        result = asyncio.run(run())
        assert result["output_contains"] == 0.5

    def test_no_expected_keywords_returns_1(self):
        """Doc: expected_output_contains None → 1.0."""
        from arf.plugins.eval.metrics import OutputContainsMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = OutputContainsMetric()
        trace = {"turns": [{"model_output": "whatever"}]}

        async def run():
            return await metric.compute(trace, EvalCase(id="c0", input="hi"))

        result = asyncio.run(run())
        assert result["output_contains"] == 1.0

    def test_case_insensitive_matching(self):
        """Doc: Case-insensitive keyword matching."""
        from arf.plugins.eval.metrics import OutputContainsMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = OutputContainsMetric()
        trace = {"turns": [{"model_output": "HELLO WORLD"}]}
        expected = EvalCase(id="c0", input="hi", expected_output_contains=["hello"])

        async def run():
            return await metric.compute(trace, expected)

        result = asyncio.run(run())
        assert result["output_contains"] == 1.0

    def test_uses_last_turn_with_output(self):
        """Doc: Uses last non-empty model_output."""
        from arf.plugins.eval.metrics import OutputContainsMetric
        from arf.core.protocols.evaluation import EvalCase

        metric = OutputContainsMetric()
        trace = {"turns": [
            {"turn": 0, "model_output": "first contains keyword"},
            {"turn": 1, "model_output": ""},
        ]}
        expected = EvalCase(id="c0", input="hi", expected_output_contains=["keyword"])

        async def run():
            return await metric.compute(trace, expected)

        result = asyncio.run(run())
        assert result["output_contains"] == 1.0


# ---------------------------------------------------------------------------
# 12. trace_adapter (docs Section 5, used by runner)
# ---------------------------------------------------------------------------

class TestTraceAdapter:
    """Doc: events_to_trace converts AgentEvent list into structured trace dict."""

    def test_events_to_trace_basic(self):
        """Doc: Converts events into {turns: [...]}."""
        from arf.plugins.eval.trace_adapter import events_to_trace
        from arf.core.events import AgentEvent

        events = [
            AgentEvent(type="tool_call_end", turn=0, data={"tool_name": "ls", "success": True, "duration_ms": 100}),
            AgentEvent(type="tool_call_end", turn=0, data={"tool_name": "cat", "success": True, "duration_ms": 50}),
            AgentEvent(type="model_call_end", turn=0, data={"content": "result"}),
        ]
        trace = events_to_trace(events)
        assert "turns" in trace
        assert len(trace["turns"]) == 1
        turn = trace["turns"][0]
        assert turn["turn"] == 0
        assert len(turn["tool_calls"]) == 2
        assert turn["tool_calls"][0]["tool_name"] == "ls"
        assert turn["model_output"] == "result"
        assert turn["duration_ms"] == 150

    def test_events_to_trace_with_error(self):
        """Doc: Error events populate turn.error."""
        from arf.plugins.eval.trace_adapter import events_to_trace
        from arf.core.events import AgentEvent

        events = [
            AgentEvent(type="error", turn=0, data={"detail": "connection timeout"}),
        ]
        trace = events_to_trace(events)
        assert trace["turns"][0]["error"] == "connection timeout"

    def test_events_to_trace_multiple_turns(self):
        """Doc: Multiple turns are sorted."""
        from arf.plugins.eval.trace_adapter import events_to_trace
        from arf.core.events import AgentEvent

        events = [
            AgentEvent(type="tool_call_end", turn=1, data={"tool_name": "cmd2", "duration_ms": 30}),
            AgentEvent(type="tool_call_end", turn=0, data={"tool_name": "cmd1", "duration_ms": 20}),
        ]
        trace = events_to_trace(events)
        assert len(trace["turns"]) == 2
        assert trace["turns"][0]["turn"] == 0
        assert trace["turns"][1]["turn"] == 1


# ---------------------------------------------------------------------------
# 13. EvalRunner._hash_config (implementation detail)
# ---------------------------------------------------------------------------

class TestRunnerHashConfig:
    """Doc: agent_config_hash is SHA256 digest of agent config."""

    def test_hash_config_returns_12_char_hex(self):
        """Doc: agent_config_hash = SHA256 digest, truncated to 12 chars."""
        from arf.plugins.eval.runner import EvalRunner
        agent = _FakeEvalAgent(config="test_config_string")
        h = EvalRunner._hash_config(agent)
        assert isinstance(h, str)
        assert len(h) == 12
        # Verify it's hex
        int(h, 16)

    def test_hash_config_fallback(self):
        """Doc: Hash failure returns 'unknown'."""
        from arf.plugins.eval.runner import EvalRunner

        class BadConfigAgent:
            @property
            def config(self):
                raise RuntimeError("corrupt config")

        h = EvalRunner._hash_config(BadConfigAgent())
        assert h == "unknown"


# ---------------------------------------------------------------------------
# 14. EvalRunner._build_summary (implementation detail)
# ---------------------------------------------------------------------------

class TestRunnerBuildSummary:
    """Doc: Summary aggregation in runner."""

    def test_build_summary_counts(self):
        """Doc: Summary computes total, passed, failed, rates."""
        from arf.plugins.eval.runner import EvalRunner
        from arf.plugins.eval.models import EvalBenchmark

        per_case = [
            {"case_id": "c0", "passed": True, "turns": 2, "tool_calls": 3, "duration_seconds": 1.0, "metrics": {"tool_accuracy": 1.0, "output_contains": 1.0}},
            {"case_id": "c1", "passed": True, "turns": 1, "tool_calls": 0, "duration_seconds": 0.5, "metrics": {"tool_accuracy": 0.5, "output_contains": 0.0}},
        ]
        bm = EvalBenchmark(name="t", cases=[])
        runner = EvalRunner.__new__(EvalRunner)
        summary = runner._build_summary(per_case, bm)
        assert summary.total == 2
        assert summary.passed == 2
        assert summary.failed == 0
        assert summary.pass_rate == 1.0
        assert summary.avg_turns == 1.5
        assert summary.avg_tool_calls == 1.5
        assert summary.avg_duration_seconds == 0.75
        assert summary.tool_accuracy == 0.75
        assert summary.output_contains == 0.5


# ---------------------------------------------------------------------------
# 15. Module __init__ exports (docs 3.x)
# ---------------------------------------------------------------------------

class TestModuleExports:
    """Doc: All public symbols exported from arf.plugins.eval."""

    def test_init_exports_all_documented_names(self):
        """Doc: arf.plugins.eval exports all documented classes."""
        import arf.plugins.eval
        expected = {
            "EvalRunner", "BenchmarkBuilder", "EvalComparator",
            "SuccessRateMetric", "ToolAccuracyMetric", "TurnEfficiencyMetric",
            "OutputContainsMetric",
            "EvalCase", "EvalBenchmark", "EvalReport", "EvalSummary", "EvalDiff",
            "EvalError", "events_to_trace",
        }
        exported = set(arf.plugins.eval.__all__)
        missing = expected - exported
        assert not missing, f"Missing from __all__: {missing}"


# ---------------------------------------------------------------------------
# 16. Verify file existence for paths in doc config section
# ---------------------------------------------------------------------------

class TestConfigPaths:
    """Doc Section 6: Benchmark/report paths are app-level, not framework-managed."""

    def test_benchmark_path_not_at_root(self):
        """benchmarks/ is app-level usage data, not a framework directory."""
        root = Path(__file__).parent.parent.parent
        p = root / "benchmarks"
        assert not p.exists(), (
            "benchmarks/ should NOT exist at repo root — it is app-level usage data"
        )

    def test_reports_path_not_at_root(self):
        """reports/ is app-level usage data, not a framework directory."""
        root = Path(__file__).parent.parent.parent
        p = root / "reports"
        assert not p.exists(), (
            "reports/ should NOT exist at repo root — it is app-level usage data"
        )

    def test_traces_path_in_config(self):
        """Doc: data/traces/ is the default trace output directory."""
        from arf.core.config_base import ObservabilityConfig
        cfg = ObservabilityConfig()
        assert cfg.trace_dir == "./data/traces", (
            f"Default should be ./data/traces, got {cfg.trace_dir}"
        )


# ---------------------------------------------------------------------------
# 17. Protocol definitions (arf/core/protocols/evaluation.py)
# ---------------------------------------------------------------------------

class TestEvaluationProtocol:
    """Doc: Protocol definitions in arf/core/protocols/evaluation.py."""

    def test_eval_dataset_exists(self):
        """Doc: EvalDataset protocol (not in doc but defined)."""
        from arf.core.protocols.evaluation import EvalDataset
        assert EvalDataset is not None

    def test_metric_calculator_protocol(self):
        """Doc: MetricCalculator protocol with async compute()."""
        from arf.core.protocols.evaluation import MetricCalculator
        assert hasattr(MetricCalculator, "compute")

    def test_eval_runner_protocol(self):
        """Doc: EvalRunner protocol with async run()."""
        from arf.core.protocols.evaluation import EvalRunner
        assert hasattr(EvalRunner, "run")
        assert inspect.iscoroutinefunction(EvalRunner.run)

    def test_protocol_eval_report_aligned_with_impl(self):
        """Protocol and implementation EvalReport now use the same field names (fixed 2026-05-29)."""
        from arf.core.protocols.evaluation import EvalReport as ProtoReport
        from arf.plugins.eval.models import EvalReport as ImplReport
        proto_fields = {f.name for f in ProtoReport.__dataclass_fields__.values()}
        impl_fields = {f.name for f in ImplReport.__dataclass_fields__.values()}
        # Both use benchmark_name now
        assert "benchmark_name" in proto_fields
        assert "benchmark_name" in impl_fields
        # comparison field removed from protocol to match impl
        assert "comparison" not in proto_fields
        # dataset_name replaced by benchmark_name
        assert "dataset_name" not in proto_fields


# ---------------------------------------------------------------------------
# 18. Exceptions
# ---------------------------------------------------------------------------

class TestEvalError:
    """Doc: EvalError from arf/plugins/eval/exceptions.py."""

    def test_eval_error_is_exception(self):
        """Doc: EvalError extends Exception."""
        from arf.plugins.eval.exceptions import EvalError
        assert issubclass(EvalError, Exception)

    def test_eval_error_raises(self):
        """Doc: EvalError can be raised."""
        from arf.plugins.eval.exceptions import EvalError
        with pytest.raises(EvalError):
            raise EvalError("test error")


# ---------------------------------------------------------------------------
# 19. CRITICAL: DefaultEvalRunner does not exist (found 2026-05-29)
# ---------------------------------------------------------------------------

class TestDefaultEvalRunnerFixed:
    """base.py now uses EvalRunner (not non-existent DefaultEvalRunner). Fixed 2026-05-29."""

    def test_eval_runner_imports(self):
        """EvalRunner is the canonical runner class."""
        from arf.plugins.eval.runner import EvalRunner
        assert EvalRunner is not None

    def test_default_eval_runner_does_not_exist(self):
        """DefaultEvalRunner was removed — only EvalRunner exists."""
        import arf.plugins.eval.runner as mod
        names = [n for n in dir(mod) if "Eval" in n or "Runner" in n]
        assert "DefaultEvalRunner" not in names


# ---------------------------------------------------------------------------
# 20. BaseAgent.evaluate() is now async and uses EvalRunner (fixed 2026-05-29)
# ---------------------------------------------------------------------------

class TestBaseAgentEvaluate:
    """base.py evaluate() is async, uses EvalRunner with benchmark + event_bus."""

    def test_evaluate_is_async(self):
        from arf.agent.base import BaseAgent
        import inspect
        assert inspect.iscoroutinefunction(BaseAgent.evaluate), (
            "evaluate() must be async to await runner.run()"
        )

    def test_evaluate_uses_eval_runner(self):
        from arf.agent.base import BaseAgent
        import inspect
        src = inspect.getsource(BaseAgent.evaluate)
        assert "EvalRunner" in src
        assert "DefaultEvalRunner" not in src

    def test_evaluate_accepts_benchmark(self):
        """2026-05-29: evaluate(benchmark) — max_parallel removed."""
        from arf.agent.base import BaseAgent
        import inspect
        sig = inspect.signature(BaseAgent.evaluate)
        params = list(sig.parameters.keys())
        assert "benchmark" in params
        assert "max_parallel" not in params


# ---------------------------------------------------------------------------
# 21. Protocol EvalSummary now includes output_contains (fixed 2026-05-29)
# ---------------------------------------------------------------------------

class TestProtocolEvalSummaryAligned:
    """Protocol EvalSummary now matches implementation with output_contains."""

    def test_protocol_has_output_contains(self):
        from arf.core.protocols.evaluation import EvalSummary as ProtoSummary
        from arf.plugins.eval.models import EvalSummary as ImplSummary
        impl_fields = {f.name for f in ImplSummary.__dataclass_fields__.values()}
        proto_fields = {f.name for f in ProtoSummary.__dataclass_fields__.values()}
        assert "output_contains" in impl_fields
        assert "output_contains" in proto_fields, (
            "Protocol EvalSummary must include output_contains to match implementation"
        )

    def test_protocol_and_impl_eval_summary_aligned(self):
        from arf.core.protocols.evaluation import EvalSummary as ProtoSummary
        from arf.plugins.eval.models import EvalSummary as ImplSummary
        impl_fields = {f.name for f in ImplSummary.__dataclass_fields__.values()}
        proto_fields = {f.name for f in ProtoSummary.__dataclass_fields__.values()}
        # All implementation fields should be in protocol (protocol may have extras)
        missing_from_proto = impl_fields - proto_fields
        assert not missing_from_proto, (
            f"Implementation EvalSummary has fields not in protocol: {missing_from_proto}"
        )


# ---------------------------------------------------------------------------
# 22. Protocol now exports EvalDiff, EvalBenchmark, BenchmarkBuilder, EvalComparator (fixed 2026-05-29)
# ---------------------------------------------------------------------------

class TestProtocolNewExports:
    """Protocol evaluation.py now exports all types matching implementation."""

    def test_protocol_has_eval_diff(self):
        from arf.core.protocols.evaluation import EvalDiff
        assert EvalDiff is not None

    def test_protocol_has_eval_benchmark(self):
        from arf.core.protocols.evaluation import EvalBenchmark
        assert EvalBenchmark is not None

    def test_protocol_has_benchmark_builder(self):
        from arf.core.protocols.evaluation import BenchmarkBuilder
        assert BenchmarkBuilder is not None

    def test_protocol_has_eval_comparator(self):
        from arf.core.protocols.evaluation import EvalComparator
        assert EvalComparator is not None

    def test_eval_dataset_is_backward_compat(self):
        """EvalDataset remains as backward-compat alias for EvalBenchmark."""
        from arf.core.protocols.evaluation import EvalDataset, EvalBenchmark
        assert EvalDataset is EvalBenchmark

    def test_protocol_eval_report_uses_benchmark_name(self):
        """Protocol EvalReport now uses benchmark_name (matching impl)."""
        from arf.core.protocols.evaluation import EvalReport as ProtoReport
        from arf.plugins.eval.models import EvalReport as ImplReport
        proto_fields = {f.name for f in ProtoReport.__dataclass_fields__.values()}
        impl_fields = {f.name for f in ImplReport.__dataclass_fields__.values()}
        assert "benchmark_name" in proto_fields
        assert "benchmark_name" in impl_fields

    def test_protocol_eval_runner_matches_impl(self):
        """Protocol EvalRunner.run matches implementation signature.
        max_parallel removed 2026-05-29 — session isolation not ready."""
        from arf.core.protocols.evaluation import EvalRunner as ProtoRunner
        from arf.plugins.eval.runner import EvalRunner as ImplRunner
        import inspect
        proto_sig = inspect.signature(ProtoRunner.run)
        impl_sig = inspect.signature(ImplRunner.run)
        proto_params = list(proto_sig.parameters.keys())
        impl_params = list(impl_sig.parameters.keys())
        assert "benchmark" in proto_params
        assert "max_parallel" not in proto_params
        assert proto_params == impl_params, (
            f"Protocol EvalRunner.run params {proto_params} != impl {impl_params}"
        )


# ===========================================================================
# NEW FINDINGS — 2026-05-29 joint fact-check
# ===========================================================================

class TestFindingsBenchmarkBuilder:
    """BenchmarkBuilder 不自动推断 expected_output_contains."""

    def test_builder_does_not_populate_expected_output_contains(self):
        """UPDATED 2026-06-11: BenchmarkBuilder.build() now populates expected_output_contains
        from final assistant content in golden trajectory."""
        from arf.plugins.eval.builder import BenchmarkBuilder
        src = inspect.getsource(BenchmarkBuilder.build)
        assert "expected_output_contains" in src, (
            "BenchmarkBuilder build() now extracts expected_output_contains from assistant content."
        )

    def test_builder_does_infer_expected_tools(self):
        """BenchmarkBuilder 确实从 tool_call_start 事件推断 expected_tools."""
        from arf.plugins.eval.builder import BenchmarkBuilder
        src = inspect.getsource(BenchmarkBuilder.build)
        assert "expected_tools" in src
        assert "tool_call_start" in src

    def test_eval_doc_no_longer_claims_auto_infer_output_keywords(self):
        """FIXED 2026-05-29: Doc 不再声称自动推断输出关键词."""
        doc_path = Path(__file__).parent.parent.parent / "docs" / "eval-benchmark.md"
        content = doc_path.read_text(encoding="utf-8")
        assert "自动推断预期工具调用和输出关键词" not in content


class TestFindingsEvalPathFixes:
    """eval-benchmark.md 残留旧路径 memory/sessions/."""

    def test_eval_doc_all_paths_use_memory_traces(self):
        """FIXED 2026-05-29: 所有路径已修正为 data/traces/."""
        doc_path = Path(__file__).parent.parent.parent / "docs" / "eval-benchmark.md"
        content = doc_path.read_text(encoding="utf-8")
        assert ("./memory/sessions" not in content
                and "memory/sessions/" not in content), (
            "FIX VERIFIED: No stale memory/sessions paths in eval-benchmark.md"
        )


class TestFindingsEvalRunnerSignature:
    """Protocol 和实现签名一致性 + max_parallel 已删除."""

    def test_protocol_eval_runner_matches_impl(self):
        """Protocol EvalRunner.run matches implementation signature."""
        from arf.core.protocols.evaluation import EvalRunner as ProtoRunner
        from arf.plugins.eval.runner import EvalRunner as ImplRunner
        import inspect
        proto_sig = inspect.signature(ProtoRunner.run)
        impl_sig = inspect.signature(ImplRunner.run)
        proto_params = list(proto_sig.parameters.keys())
        impl_params = list(impl_sig.parameters.keys())
        assert "benchmark" in proto_params
        assert proto_params == impl_params, (
            f"Protocol EvalRunner.run params {proto_params} != impl {impl_params}"
        )

    def test_max_parallel_removed_from_signatures(self):
        """FIXED 2026-05-29: max_parallel 已从协议和实现中删除.
        参数在 session 级状态隔离就绪前不承诺该能力."""
        from arf.core.protocols.evaluation import EvalRunner as ProtoRunner
        from arf.plugins.eval.runner import EvalRunner as ImplRunner
        import inspect
        proto_sig = inspect.signature(ProtoRunner.run)
        impl_sig = inspect.signature(ImplRunner.run)
        assert "max_parallel" not in proto_sig.parameters
        assert "max_parallel" not in impl_sig.parameters
        # 演进计划已记录在 docs/eval-benchmark.md §7
