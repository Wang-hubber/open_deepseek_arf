"""Evaluation — benchmark builder, runner, comparator, and metrics."""
from arf.plugins.eval.runner import EvalRunner
from arf.plugins.eval.builder import BenchmarkBuilder
from arf.plugins.eval.comparator import EvalComparator
from arf.plugins.eval.metrics import (
    SuccessRateMetric, ToolCallAccuracyMetric, ToolCallResultLLMMetric,
    TurnEfficiencyMetric, ExecutionAccuracyMetric, ReasoningSimilarityMetric,
    OutputQualityMetric, TrajectorySimilarityMetric,
)
from arf.plugins.eval.models import (
    EvalCase, EvalBenchmark, EvalReport, EvalSummary, EvalDiff,
    EvalConfig, JudgeModelConfig,
)
from arf.plugins.eval.exceptions import EvalError
from arf.plugins.eval.trace_adapter import events_to_trace

__all__ = [
    "EvalRunner", "BenchmarkBuilder", "EvalComparator",
    "SuccessRateMetric", "ToolCallAccuracyMetric", "ToolCallResultLLMMetric",
    "TurnEfficiencyMetric", "ExecutionAccuracyMetric", "ReasoningSimilarityMetric",
    "OutputQualityMetric", "TrajectorySimilarityMetric",
    "EvalCase", "EvalBenchmark", "EvalReport", "EvalSummary", "EvalDiff",
    "EvalConfig", "JudgeModelConfig",
    "EvalError", "events_to_trace",
]
