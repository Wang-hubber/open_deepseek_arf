"""Unit tests for eval data models and JSON serialization."""
import pytest

from arf.plugins.eval.models import EvalCase, EvalBenchmark


class TestEvalCase:
    def test_minimal(self):
        c = EvalCase(id="c1", input="hello")
        assert c.expected_execution == []
        assert c.expected_output_contains == []
        assert c.feedback is None
        assert c.source_round is None

    def test_full(self):
        c = EvalCase(id="c1", input="hello",
                     expected_execution=[{"type": "tool", "name": "file_writer", "params": {}}],
                     expected_output_contains=["hello.py"],
                     max_turns=3)
        assert c.max_turns == 3


class TestEvalBenchmarkJson:
    @pytest.fixture
    def benchmark(self):
        return EvalBenchmark(
            name="file_ops_v1",
            source_session="default",
            created_at=1716812345.0,
            cases=[
                EvalCase(id="c0", input="create hello.py",
                         expected_execution=[{"type": "tool", "name": "file_writer", "params": {}}],
                         expected_output_contains=["hello.py"]),
                EvalCase(id="c1", input="read it back"),
            ],
        )

    def test_to_json_roundtrip(self, benchmark, tmp_path):
        p = tmp_path / "bm.json"
        benchmark.to_json(str(p))
        loaded = EvalBenchmark.from_json(str(p))
        assert loaded.name == "file_ops_v1"
        assert loaded.source_session == "default"
        assert len(loaded.cases) == 2
        assert loaded.cases[0].input == "create hello.py"
        assert loaded.cases[0].expected_execution == [{"type": "tool", "name": "file_writer", "params": {}}]

    def test_from_json_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            EvalBenchmark.from_json(str(tmp_path / "nope.json"))

    def test_feedback_roundtrip(self, benchmark, tmp_path):
        benchmark.cases[0].feedback = {"rating": "good", "reason": "works well"}
        p = tmp_path / "bm.json"
        benchmark.to_json(str(p))
        loaded = EvalBenchmark.from_json(str(p))
        assert loaded.cases[0].feedback == {"rating": "good", "reason": "works well"}

    def test_source_round_roundtrip(self, benchmark, tmp_path):
        benchmark.cases[0].source_round = 1
        p = tmp_path / "bm.json"
        benchmark.to_json(str(p))
        loaded = EvalBenchmark.from_json(str(p))
        assert loaded.cases[0].source_round == 1

    def test_defaults(self):
        bm = EvalBenchmark(name="test")
        assert bm.cases == []
        assert bm.source_session is None

from arf.plugins.eval.models import EvalSummary, EvalReport, EvalDiff, EvalConfig


class TestEvalReportJson:
    @pytest.fixture
    def report(self):
        return EvalReport(
            run_id="run-001",
            benchmark_name="file_ops_v1",
            agent_config_hash="abc123",
            timestamp=1716812345.0,
            summary=EvalSummary(
                total=2, passed=2, failed=0, pass_rate=1.0,
                avg_turns=1.5, avg_tool_calls=1.0, avg_duration_seconds=2.0,
                tool_accuracy=1.0, output_contains=1.0,
            ),
            per_case=[
                {"case_id": "c0", "passed": True, "trace": {"turns": []},
                 "metrics": {"success_rate": 1.0}, "response": "ok"},
            ],
        )

    def test_report_to_json_roundtrip(self, report, tmp_path):
        p = tmp_path / "report.json"
        report.to_json(str(p))
        loaded = EvalReport.from_json(str(p))
        assert loaded.run_id == "run-001"
        assert loaded.benchmark_name == "file_ops_v1"
        assert loaded.summary.pass_rate == 1.0

    def test_report_defaults(self):
        r = EvalReport(run_id="r", benchmark_name="b",
                       agent_config_hash="", timestamp=0.0)
        assert r.summary.total == 0


class TestEvalDiff:
    def test_diff_structure(self):
        diff = EvalDiff(
            baseline_run_id="r1", current_run_id="r2",
            summary_diff={"pass_rate": -0.1},
            regressions=[{"case_id": "c0", "metric": "tool_accuracy", "delta": -0.5}],
            improvements=[],
        )
        assert len(diff.regressions) == 1
        assert diff.summary_diff["pass_rate"] == -0.1


class TestEvalConfig:
    def test_validate_no_judge_with_llm_metrics(self):
        config = EvalConfig(
            metrics={"output_quality": True, "trajectory_similarity": False},
            judge=None,
        )
        with pytest.raises(ValueError, match="LLM-as-judge"):
            config.validate()

    def test_validate_offline_without_traces(self):
        config = EvalConfig(mode="offline", trace_session_ids=[])
        with pytest.raises(ValueError, match="trace_session_ids"):
            config.validate()

    def test_validate_ok(self):
        config = EvalConfig()
        config.validate()  # should not raise

    def test_validate_judge_model_required_with_llm_metrics(self):
        """When LLM metrics enabled, both judge and judge_model must be present."""
        from arf.plugins.eval.models import JudgeModelConfig

        # Missing judge_model
        config = EvalConfig(
            metrics={"output_quality": True},
            judge=JudgeModelConfig(),
            judge_model=None,
        )
        with pytest.raises(ValueError, match="judge_model"):
            config.validate()

    def test_validate_ok_with_judge_model(self):
        """validate() passes when both judge and judge_model are provided."""
        from arf.core.model_registry import ResolvedModelConfig
        from arf.plugins.eval.models import JudgeModelConfig

        config = EvalConfig(
            metrics={"output_quality": True, "tool_call_result_llm": True},
            judge=JudgeModelConfig(),
            judge_model=ResolvedModelConfig(
                model="gpt-4",
                api_base="https://api.openai.com/v1",
                api_key_env="OPENAI_API_KEY",
            ),
        )
        config.validate()  # should not raise

    def test_validate_ok_no_judge_when_no_llm_metrics(self):
        """When no LLM metrics, judge and judge_model can both be None."""
        config = EvalConfig(
            metrics={"success_rate": True, "tool_call_accuracy": True},
            judge=None,
            judge_model=None,
        )
        config.validate()  # should not raise


class TestEvalCaseContextMessages:
    def test_context_messages_default_empty(self):
        c = EvalCase(id="c1", input="hello")
        assert c.context_messages == []

    def test_context_messages_roundtrip(self, tmp_path):
        bm = EvalBenchmark(
            name="test",
            cases=[
                EvalCase(
                    id="c0", input="hello",
                    context_messages=[
                        {"role": "assistant", "content": "I found 3 files"},
                        {"role": "tool", "tool_call_id": "t1", "content": "a.txt\nb.txt"},
                    ],
                ),
            ],
        )
        p = tmp_path / "bm.json"
        bm.to_json(str(p))
        loaded = EvalBenchmark.from_json(str(p))
        assert loaded.cases[0].context_messages == [
            {"role": "assistant", "content": "I found 3 files"},
            {"role": "tool", "tool_call_id": "t1", "content": "a.txt\nb.txt"},
        ]

    def test_context_messages_omitted_when_empty(self, tmp_path):
        bm = EvalBenchmark(name="test", cases=[EvalCase(id="c0", input="hi")])
        p = tmp_path / "bm.json"
        bm.to_json(str(p))
        text = p.read_text()
        assert "context_messages" not in text


class TestEvalConfigScoringWeights:
    def test_default_scoring_weights(self):
        config = EvalConfig()
        assert config.scoring_weights["tool_call_accuracy"] == 0.2
        assert config.scoring_weights["output_quality"] == 0.15

    def test_custom_scoring_weights(self):
        config = EvalConfig(scoring_weights={"tool_call_accuracy": 0.5, "output_quality": 0.5})
        assert config.scoring_weights["tool_call_accuracy"] == 0.5


class TestEvalReportAgentSnapshot:
    def test_agent_snapshot_default_empty(self):
        report = EvalReport(run_id="r1", benchmark_name="bm", agent_config_hash="h1", timestamp=1.0)
        assert report.agent_snapshot == {}

    def test_agent_snapshot_roundtrip(self, tmp_path):
        report = EvalReport(
            run_id="r1", benchmark_name="bm", agent_config_hash="h1", timestamp=1.0,
            agent_snapshot={"hash": "abc", "config": {"model": {"name": "deepseek-chat"}}},
        )
        p = tmp_path / "report.json"
        report.to_json(str(p))
        loaded = EvalReport.from_json(str(p))
        assert loaded.agent_snapshot["hash"] == "abc"
        assert loaded.agent_snapshot["config"]["model"]["name"] == "deepseek-chat"

    def test_old_report_without_agent_snapshot_loads(self, tmp_path):
        """向后兼容：旧 report JSON 没有 agent_snapshot 字段"""
        p = tmp_path / "old_report.json"
        import json
        with open(str(p), "w") as f:
            json.dump({
                "run_id": "r1", "benchmark_name": "bm", "agent_config_hash": "h1",
                "timestamp": 1.0, "summary": {"total": 1, "passed": 1, "failed": 0,
                "pass_rate": 1.0, "avg_turns": 1.0, "avg_tool_calls": 0.0,
                "avg_duration_seconds": 0.0, "total_tokens_in": 0, "total_tokens_out": 0,
                "total_duration_seconds": 0.0, "tool_accuracy": 1.0, "output_contains": 1.0,
                "tool_call_accuracy": 1.0, "turn_efficiency": 1.0, "success_rate": 1.0,
                "execution_accuracy": 1.0},
                "per_case": [], "judge_model": "", "metrics_enabled": [], "mode": "online",
                "snapshot_hash": "",
            }, f)
        loaded = EvalReport.from_json(str(p))
        assert loaded.agent_snapshot == {}
