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

        trace_dir = tmpdir / "s1" / "traces"
        trace_dir.mkdir(parents=True)
        trace_file = trace_dir / "s1.jsonl"
        trace_file.write_text(json.dumps({
            "type": "model_call_end", "turn": 1,
            "data": {"content": "hello world"},
            "timestamp": 1.0, "session_id": "s1",
        }) + "\n", encoding="utf-8")

        config = EvalConfig(
            benchmark_path=bm_path,
            data_dir=str(tmpdir),
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


class TestCaseIsolation:
    """Verify each case gets a unique session_id."""

    @pytest.fixture
    def tmpdir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_each_case_gets_unique_session(self, tmpdir):
        """Cases should NOT share session_ids even from same source session."""
        from arf.plugins.eval.runner import EvalRunner

        bm_path = str(tmpdir / "bm.json")
        cases = [
            EvalCase(id="c0", input="hello", session_id="src_s1"),
            EvalCase(id="c1", input="world", session_id="src_s1"),
        ]
        _make_benchmark(bm_path, cases=cases)

        sid_records = []

        class FakeAgent:
            class _PrimitiveAgent:
                def input(self, *, role, content):
                    pass
            def __init__(self):
                self._primitive_agent = self._PrimitiveAgent()
            async def run(self, user_message, *, session_id):
                sid_records.append(session_id)
                return "ok"

        config = EvalConfig(benchmark_path=bm_path, data_dir=str(tmpdir),
                            judge_model=None)
        agent = FakeAgent()

        import asyncio
        runner = EvalRunner(config)
        asyncio.run(runner.run_online(agent))

        # Two cases from same source session should have DIFFERENT eval sessions
        assert sid_records[0] != sid_records[1]
        assert len(set(sid_records)) == 2

    def test_context_messages_injected_before_chat(self, tmpdir):
        """context_messages should be injected via agent._primitive_agent.input() before chat."""
        from arf.plugins.eval.runner import EvalRunner

        bm_path = str(tmpdir / "bm.json")
        context = [
            {"role": "assistant", "content": "I found file.txt"},
            {"role": "tool", "tool_call_id": "t1", "content": "hello world"},
        ]
        cases = [EvalCase(id="c0", input="read it", context_messages=context)]
        _make_benchmark(bm_path, cases=cases)

        injected_messages = []
        chat_messages = []

        class FakeAgent:
            class _PrimitiveAgent:
                def input(self, *, role, content):
                    injected_messages.append({"role": role, "content": content})
            def __init__(self):
                self._primitive_agent = self._PrimitiveAgent()
            async def run(self, user_message, *, session_id):
                chat_messages.append(user_message)
                return "done"

        config = EvalConfig(benchmark_path=bm_path, data_dir=str(tmpdir),
                            judge_model=None)
        agent = FakeAgent()

        import asyncio
        runner = EvalRunner(config)
        asyncio.run(runner.run_online(agent))

        # context_messages injected in order
        assert len(injected_messages) == 2
        assert injected_messages[0]["role"] == "assistant"
        assert injected_messages[0]["content"] == "I found file.txt"
        assert injected_messages[1]["role"] == "tool"
        assert injected_messages[1]["content"] == "hello world"
        # Then the real input
        assert chat_messages == ["read it"]

    def test_empty_context_messages_no_op(self, tmpdir):
        """When context_messages is empty, no injection happens."""
        from arf.plugins.eval.runner import EvalRunner

        bm_path = str(tmpdir / "bm.json")
        cases = [EvalCase(id="c0", input="hello")]
        _make_benchmark(bm_path, cases=cases)

        class FakeAgent:
            class _PrimitiveAgent:
                def input(self, *, role, content):
                    raise AssertionError("should not be called")
            def __init__(self):
                self._primitive_agent = self._PrimitiveAgent()
            async def run(self, user_message, *, session_id):
                return "ok"

        config = EvalConfig(benchmark_path=bm_path, data_dir=str(tmpdir),
                            judge_model=None)
        agent = FakeAgent()

        import asyncio
        runner = EvalRunner(config)
        report = asyncio.run(runner.run_online(agent))
        assert len(report.per_case) == 1


class TestAgentSnapshot:
    """Verify agent_snapshot is read from snapshot_created event."""

    @pytest.fixture
    def tmpdir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_agent_snapshot_in_report(self, tmpdir):
        from arf.plugins.eval.runner import EvalRunner

        bm_path = str(tmpdir / "bm.json")
        cases = [EvalCase(id="c0", input="hello")]
        _make_benchmark(bm_path, cases=cases)

        class FakeAgent:
            class _PrimitiveAgent:
                def input(self, *, role, content):
                    pass
            def __init__(self):
                self._primitive_agent = self._PrimitiveAgent()

            async def run(self, user_message, *, session_id):
                # Simulate snapshot_created event being written to trace
                import json
                trace_dir = Path(tmpdir) / session_id / "traces"
                trace_dir.mkdir(parents=True, exist_ok=True)
                trace_file = trace_dir / f"{session_id}.jsonl"
                snapshot_event = {
                    "type": "snapshot_created",
                    "session_id": session_id,
                    "turn": 0,
                    "timestamp": 1.0,
                    "data": {
                        "hash": "abc123def456",
                        "config": {
                            "model": {"name": "deepseek-chat"},
                            "tools": {"file_writer": {}},
                            "plugins": {"eval": {}},
                        },
                    },
                }
                event = {
                    "type": "model_call_end",
                    "session_id": session_id,
                    "turn": 1,
                    "timestamp": 2.0,
                    "data": {"content": "hello world"},
                }
                with open(trace_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(snapshot_event, ensure_ascii=False) + "\n")
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
                return "hello world"

        config = EvalConfig(
            benchmark_path=bm_path,
            data_dir=str(tmpdir),
            judge_model=None,
        )
        agent = FakeAgent()

        import asyncio
        runner = EvalRunner(config)
        report = asyncio.run(runner.run_online(agent))

        assert report.agent_snapshot == {
            "hash": "abc123def456",
            "config": {
                "model": {"name": "deepseek-chat"},
                "tools": {"file_writer": {}},
                "plugins": {"eval": {}},
            },
        }


class TestScoringWeights:
    def test_weighted_score_with_missing_metrics(self):
        """weighted_score = Σ(score_i × weight_i) with rebalancing for missing metrics."""
        from arf.plugins.eval.runner import EvalRunner

        metrics = {
            "tool_call_accuracy": 1.0,
            "execution_accuracy": 0.8,
            "output_contains": 1.0,
            "success_rate": 1.0,
            # output_quality and trajectory_similarity missing
        }

        weights = {
            "tool_call_accuracy": 0.2,
            "execution_accuracy": 0.2,
            "output_contains": 0.2,
            "success_rate": 0.2,
            "output_quality": 0.1,        # missing → weight redistributed
            "trajectory_similarity": 0.1,  # missing → weight redistributed
            "turn_efficiency": 0.0,
        }

        ws = EvalRunner._compute_weighted_score(metrics, weights)

        # Available: tool_call_accuracy=1.0, execution_accuracy=0.8,
        #   output_contains=1.0, success_rate=1.0
        # Their weights: 0.2, 0.2, 0.2, 0.2 = 0.8 total
        # Rebalanced: 0.2/0.8 = 0.25 each
        # = 1.0*0.25 + 0.8*0.25 + 1.0*0.25 + 1.0*0.25 = 0.95
        assert ws == pytest.approx(0.95, abs=0.01)

    def test_weighted_score_all_metrics_present(self):
        from arf.plugins.eval.runner import EvalRunner

        metrics = {
            "tool_call_accuracy": 0.9,
            "execution_accuracy": 1.0,
            "turn_efficiency": 0.8,
            "output_contains": 1.0,
            "success_rate": 1.0,
            "output_quality": 4,
            "trajectory_similarity": 3,
        }

        weights = {
            "tool_call_accuracy": 0.15,
            "execution_accuracy": 0.15,
            "turn_efficiency": 0.1,
            "output_contains": 0.1,
            "success_rate": 0.15,
            "output_quality": 0.2,
            "trajectory_similarity": 0.15,
        }

        ws = EvalRunner._compute_weighted_score(metrics, weights)

        # LLM metrics normalized: 4/5=0.8, 3/5=0.6
        # = 0.9*0.15 + 1.0*0.15 + 0.8*0.1 + 1.0*0.1 + 1.0*0.15 + 0.8*0.2 + 0.6*0.15
        # = 0.135 + 0.15 + 0.08 + 0.1 + 0.15 + 0.16 + 0.09 = 0.865
        assert ws == pytest.approx(0.865, abs=0.01)
