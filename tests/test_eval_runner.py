"""Tests for EvalRunner with EvalConfig API."""
import json
import tempfile
from pathlib import Path

import pytest

from arf.plugins.eval.runner import EvalRunner
from arf.plugins.eval.models import EvalConfig, EvalBenchmark, EvalCase


def _make_benchmark(path: str, name="test_bm", cases=None):
    if cases is None:
        cases = [EvalCase(id="c0", input="hello")]
    bm = EvalBenchmark(name=name, cases=cases)
    bm.to_json(path)


class TestEvalRunnerOffline:
    @pytest.fixture
    def tmpdir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_offline_reads_trace_and_produces_report(self, tmpdir):
        # Setup: write benchmark and trace files
        bm_path = str(tmpdir / "bm.json")
        _make_benchmark(bm_path)

        trace_dir = tmpdir / "traces"
        trace_dir.mkdir()
        trace_file = trace_dir / "s1.jsonl"
        trace_file.write_text(json.dumps({
            "type": "model_call_end", "turn": 1,
            "data": {"content": "hello world"},
            "timestamp": 1.0, "session_id": "s1",
        }) + "\n", encoding="utf-8")

        config = EvalConfig(
            benchmark_path=bm_path,
            trace_dir=str(trace_dir),
            mode="offline",
            trace_session_ids=["s1"],
            metrics={
                "success_rate": True,
                "tool_call_accuracy": True,
                "turn_efficiency": True,
                "output_quality": False,
                "trajectory_similarity": False,
            },
        )

        import asyncio
        runner = EvalRunner(config)
        report = asyncio.run(runner.run_offline())

        assert report.benchmark_name == "test_bm"
        assert report.mode == "offline"
        assert len(report.per_case) == 1
        assert report.per_case[0]["case_id"] == "c0"
        assert "success_rate" in report.metrics_enabled
        assert report.per_case[0]["metrics"]["success_rate"] == 1.0
        assert report.per_case[0]["metrics"]["tool_call_accuracy"] == 1.0


class TestEvalConfigValidation:
    def test_llm_metrics_without_judge_raises(self):
        config = EvalConfig(
            metrics={"output_quality": True},
        )
        with pytest.raises(ValueError, match="LLM-as-judge"):
            config.validate()

    def test_offline_without_traces_raises(self):
        config = EvalConfig(mode="offline", trace_session_ids=[])
        with pytest.raises(ValueError, match="trace_session_ids"):
            config.validate()
