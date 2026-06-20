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

from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext


class EvalPlugin(_NewPlugin):
    """Benchmark evaluation plugin. No lifecycle hooks by default."""

    def __init__(self, name="eval", events=None, config=None):
        super().__init__(name=name, events=events or [], config=config or {})

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        pass


Plugin = EvalPlugin

__all__ = [
    "EvalRunner", "BenchmarkBuilder", "EvalComparator",
    "SuccessRateMetric", "ToolCallAccuracyMetric", "ToolCallResultLLMMetric",
    "TurnEfficiencyMetric", "ExecutionAccuracyMetric", "ReasoningSimilarityMetric",
    "OutputQualityMetric", "TrajectorySimilarityMetric",
    "EvalCase", "EvalBenchmark", "EvalReport", "EvalSummary", "EvalDiff",
    "EvalConfig", "JudgeModelConfig",
    "EvalError", "events_to_trace",
    "EvalPlugin", "Plugin",
]
