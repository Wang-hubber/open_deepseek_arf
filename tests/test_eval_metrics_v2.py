"""Tests for EvalMetric implementations."""
import pytest
from arf.evaluation.metrics import (
    SuccessRateMetric, ToolCallAccuracyMetric, TurnEfficiencyMetric,
)
from arf.evaluation.models import EvalCase


class TestRuleMetrics:
    def test_success_rate_no_errors(self):
        m = SuccessRateMetric()
        trace = [{"type": "model_call_end", "turn": 1,
                  "data": {"content": "ok"}, "timestamp": 1.0}]
        result = m.compute_sync(trace, EvalCase(id="c0", input="hi"))
        assert result["success_rate"] == 1.0

    def test_success_rate_with_error(self):
        m = SuccessRateMetric()
        trace = [{"type": "error", "turn": 1,
                  "data": {"detail": "boom"}, "timestamp": 1.0}]
        result = m.compute_sync(trace, EvalCase(id="c0", input="hi"))
        assert result["success_rate"] == 0.0

    def test_tool_call_accuracy_exact_match(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read"}, "timestamp": 1.0},
            {"type": "tool_call_start", "turn": 2,
             "data": {"tool_name": "glob"}, "timestamp": 2.0},
        ]
        c = EvalCase(id="c0", input="hi", expected_tools=["read", "glob"])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 1.0

    def test_tool_call_accuracy_partial(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read"}, "timestamp": 1.0},
        ]
        c = EvalCase(id="c0", input="hi", expected_tools=["read", "glob"])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 0.5

    def test_turn_efficiency(self):
        m = TurnEfficiencyMetric()
        trace = [
            {"type": "x", "turn": 1, "data": {}, "timestamp": 1.0},
            {"type": "x", "turn": 1, "data": {}, "timestamp": 1.1},
            {"type": "x", "turn": 2, "data": {}, "timestamp": 2.0},
        ]
        c = EvalCase(id="c0", input="hi", max_turns=1)
        result = m.compute_sync(trace, c)
        assert result["turn_efficiency"] <= 0.5

    def test_no_expected_tools_returns_1(self):
        m = ToolCallAccuracyMetric()
        trace = []
        c = EvalCase(id="c0", input="hi")
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 1.0

    def test_no_actual_tools_returns_0(self):
        m = ToolCallAccuracyMetric()
        trace = []
        c = EvalCase(id="c0", input="hi", expected_tools=["read"])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 0.0
