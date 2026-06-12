"""Unit tests for eval data models and JSON serialization."""
import pytest

from arf.plugins.eval.models import EvalCase, EvalBenchmark


class TestEvalCase:
    def test_minimal(self):
        c = EvalCase(id="c1", input="hello")
        assert c.expected_tools is None
        assert c.expected_output_contains is None

    def test_full(self):
        c = EvalCase(id="c1", input="hello",
                     expected_tools=["file_writer"],
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
                         expected_tools=["file_writer"],
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
        assert loaded.cases[0].expected_tools == ["file_writer"]

    def test_from_json_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            EvalBenchmark.from_json(str(tmp_path / "nope.json"))

    def test_defaults(self):
        bm = EvalBenchmark(name="test")
        assert bm.cases == []
        assert bm.source_session is None

    def test_golden_trajectory_roundtrip(self, tmp_path):
        bm = EvalBenchmark(
            name="gt_test",
            cases=[
                EvalCase(
                    id="c0", input="hello",
                    golden_trajectory={
                        "turns": [{
                            "turn": 1,
                            "assistant": {"content": "hi", "tool_calls": []},
                            "tool_results": [],
                            "assistant_final": {"content": "done"},
                        }]
                    },
                ),
            ],
        )
        p = tmp_path / "bm.json"
        bm.to_json(str(p))
        loaded = EvalBenchmark.from_json(str(p))
        assert loaded.cases[0].golden_trajectory == bm.cases[0].golden_trajectory
        assert loaded.cases[0].golden_trajectory["turns"][0]["turn"] == 1


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
