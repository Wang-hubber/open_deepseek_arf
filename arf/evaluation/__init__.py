"""Evaluation — benchmark builder, runner, comparator, and metrics."""
from arf.evaluation.runner import EvalRunner
from arf.evaluation.builder import BenchmarkBuilder
from arf.evaluation.comparator import EvalComparator
from arf.evaluation.metrics import SuccessRateMetric, ToolAccuracyMetric, TurnEfficiencyMetric, OutputContainsMetric
from arf.evaluation.models import EvalCase, EvalBenchmark, EvalReport, EvalSummary, EvalDiff
from arf.evaluation.exceptions import EvalError
from arf.evaluation.trace_adapter import events_to_trace

__all__ = [
    "EvalRunner", "BenchmarkBuilder", "EvalComparator",
    "SuccessRateMetric", "ToolAccuracyMetric", "TurnEfficiencyMetric", "OutputContainsMetric",
    "EvalCase", "EvalBenchmark", "EvalReport", "EvalSummary", "EvalDiff",
    "EvalError", "events_to_trace",
]
