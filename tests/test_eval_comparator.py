"""Unit tests for EvalComparator."""
import pytest

from arf.plugins.eval.models import EvalReport, EvalSummary, EvalDiff
from arf.plugins.eval.comparator import EvalComparator
from arf.plugins.eval.exceptions import EvalError


class TestEvalComparator:
    @pytest.fixture
    def baseline(self):
        return EvalReport(
            run_id="r1", benchmark_name="bm1",
            agent_config_hash="aaa", timestamp=1000.0,
            summary=EvalSummary(
                total=2, passed=2, failed=0, pass_rate=1.0,
                avg_turns=2.0, avg_tool_calls=1.5,
                avg_duration_seconds=3.0,
                tool_accuracy=1.0, output_contains=1.0,
            ),
            per_case=[
                {"case_id": "c0", "passed": True, "metrics": {"tool_accuracy": 1.0, "output_contains": 1.0}},
                {"case_id": "c1", "passed": True, "metrics": {"tool_accuracy": 1.0, "output_contains": 1.0}},
            ],
        )

    @pytest.fixture
    def current_worse(self):
        return EvalReport(
            run_id="r2", benchmark_name="bm1",
            agent_config_hash="bbb", timestamp=2000.0,
            summary=EvalSummary(
                total=2, passed=1, failed=1, pass_rate=0.5,
                avg_turns=3.0, avg_tool_calls=2.0,
                avg_duration_seconds=5.0,
                tool_accuracy=0.5, output_contains=0.75,
            ),
            per_case=[
                {"case_id": "c0", "passed": True, "metrics": {"tool_accuracy": 1.0, "output_contains": 1.0}},
                {"case_id": "c1", "passed": False, "metrics": {"tool_accuracy": 0.0, "output_contains": 0.5}},
            ],
        )

    def test_compare_produces_diff(self, baseline, current_worse):
        diff = EvalComparator().compare(baseline, current_worse)
        assert diff.summary_diff["pass_rate"] == -0.5
        assert diff.summary_diff["tool_accuracy"] == -0.5
        assert len(diff.regressions) > 0
        assert len(diff.improvements) == 0

    def test_compare_different_benchmarks_raises(self, baseline):
        other = EvalReport(
            run_id="rx", benchmark_name="bm2",
            agent_config_hash="x", timestamp=0.0,
        )
        with pytest.raises(EvalError, match="different benchmark"):
            EvalComparator().compare(baseline, other)
