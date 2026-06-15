"""Tests for EvalMetric implementations."""
import json
import pytest
from arf.plugins.eval.metrics import (
    SuccessRateMetric, ToolCallAccuracyMetric, TurnEfficiencyMetric,
    ToolCallResultLLMMetric, ExecutionAccuracyMetric, ReasoningSimilarityMetric,
)
from arf.plugins.eval.models import EvalCase


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

    # --- ToolCallAccuracyMetric: name-only (backward compat) ---

    def test_tool_call_accuracy_exact_match(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read"}, "timestamp": 1.0},
            {"type": "tool_call_start", "turn": 2,
             "data": {"tool_name": "glob"}, "timestamp": 2.0},
        ]
        c = EvalCase(id="c0", input="hi",
                     expected_execution=[
                         {"type": "tool", "name": "read", "params": {}},
                         {"type": "tool", "name": "glob", "params": {}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 1.0

    def test_tool_call_accuracy_partial(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read"}, "timestamp": 1.0},
        ]
        c = EvalCase(id="c0", input="hi",
                     expected_execution=[
                         {"type": "tool", "name": "read", "params": {}},
                         {"type": "tool", "name": "glob", "params": {}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 0.5

    def test_no_expected_tools_returns_1(self):
        m = ToolCallAccuracyMetric()
        trace = []
        c = EvalCase(id="c0", input="hi")
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 1.0

    def test_no_actual_tools_returns_0(self):
        m = ToolCallAccuracyMetric()
        trace = []
        c = EvalCase(id="c0", input="hi",
                     expected_execution=[{"type": "tool", "name": "read", "params": {}}])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 0.0

    # --- ToolCallAccuracyMetric: params matching (new) ---

    def test_params_exact_match(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "eat",
                      "arguments": '{"name": "良子", "path": "良子的焖子"}'},
             "timestamp": 1.0},
        ]
        c = EvalCase(id="c0", input="eat 良子",
                     expected_execution=[
                         {"type": "tool", "name": "eat",
                          "params": {"name": "良子", "path": "良子的焖子"}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 1.0

    def test_params_subset_match(self):
        """actual has extra params beyond expected — still matches."""
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read",
                      "arguments": '{"path": "/tmp/x", "_workspace": "/ws"}'},
             "timestamp": 1.0},
        ]
        c = EvalCase(id="c0", input="read file",
                     expected_execution=[
                         {"type": "tool", "name": "read", "params": {"path": "/tmp/x"}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 1.0

    def test_params_mismatch_fails(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "eat",
                      "arguments": '{"name": "良子"}'},
             "timestamp": 1.0},
        ]
        c = EvalCase(id="c0", input="eat",
                     expected_execution=[
                         {"type": "tool", "name": "eat",
                          "params": {"name": "良子", "path": "良子的焖子"}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] < 1.0

    def test_params_name_mismatch_fails(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read",
                      "arguments": '{"path": "/tmp/x"}'},
             "timestamp": 1.0},
        ]
        c = EvalCase(id="c0", input="read",
                     expected_execution=[
                         {"type": "tool", "name": "eat", "params": {"path": "/tmp/x"}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 0.0

    def test_params_extra_actual_calls_lowers_score(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read",
                      "arguments": '{"path": "/tmp/x"}'},
             "timestamp": 1.0},
            {"type": "tool_call_start", "turn": 2,
             "data": {"tool_name": "write",
                      "arguments": '{"path": "/tmp/y"}'},
             "timestamp": 2.0},
        ]
        c = EvalCase(id="c0", input="read",
                     expected_execution=[
                         {"type": "tool", "name": "read", "params": {"path": "/tmp/x"}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 0.5

    def test_params_subset_string_contains(self):
        """string params use substring matching ('焖子' in '良子的焖子')."""
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "eat",
                      "arguments": '{"name": "良子", "path": "良子的焖子"}'},
             "timestamp": 1.0},
        ]
        c = EvalCase(id="c0", input="eat",
                     expected_execution=[
                         {"type": "tool", "name": "eat",
                          "params": {"name": "良子", "path": "焖子"}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["tool_call_accuracy"] == 1.0

    def test_dependency_order_failures_counted(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "plan_dispatch",
                      "arguments": '{"step_index": 3}'},
             "timestamp": 1.0},
            {"type": "tool_call_end", "turn": 1,
             "data": {"tool_name": "plan_dispatch",
                      "success": False,
                      "error": "step 3 depends_on step 1 which is not complete"},
             "timestamp": 1.1},
            {"type": "tool_call_start", "turn": 2,
             "data": {"tool_name": "plan_create",
                      "arguments": '{"task": "x", "steps": [...]}'},
             "timestamp": 2.0},
        ]
        c = EvalCase(id="c0", input="plan something",
                     expected_execution=[
                         {"type": "tool", "name": "plan_create", "params": {}},
                         {"type": "tool", "name": "plan_dispatch", "params": {}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["dependency_order_failures"] == 1
        # plan_dispatch matched by name, plan_create matched by name = 2/2
        assert result["tool_call_accuracy"] > 0.5

    def test_dependency_failures_not_counted_for_other_errors(self):
        m = ToolCallAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read",
                      "arguments": '{"path": "/nonexistent"}'},
             "timestamp": 1.0},
            {"type": "tool_call_end", "turn": 1,
             "data": {"tool_name": "read",
                      "success": False,
                      "error": "FileNotFoundError: /nonexistent"},
             "timestamp": 1.1},
        ]
        c = EvalCase(id="c0", input="read nonexistent",
                     expected_execution=[{"type": "tool", "name": "read", "params": {}}])
        result = m.compute_sync(trace, c)
        assert "dependency_order_failures" not in result

    # --- TurnEfficiencyMetric ---

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


class TestToolCallResultLLMMetric:
    def test_name(self):
        m = ToolCallResultLLMMetric()
        assert m.name == "tool_call_result_llm"

    def test_requires_llm(self):
        m = ToolCallResultLLMMetric()
        assert m.requires_llm is True

    def test_no_expected_tool_calls_returns_1(self):
        m = ToolCallResultLLMMetric()
        result = m.compute_sync(
            [], EvalCase(id="c0", input="hi"), judge=None
        )
        assert result["tool_call_result_llm"] == 1.0

    def test_no_results_in_expected_returns_1(self):
        """expected_tool_calls without result fields should return 1.0."""
        m = ToolCallResultLLMMetric()
        c = EvalCase(id="c0", input="hi",
                     expected_execution=[
                         {"type": "tool", "name": "read", "params": {"path": "/x"}},
                     ])
        result = m.compute_sync([], c, judge=None)
        assert result["tool_call_result_llm"] == 1.0

    def test_no_judge_returns_0(self):
        m = ToolCallResultLLMMetric()
        c = EvalCase(id="c0", input="hi",
                     expected_execution=[
                         {"type": "tool", "name": "read", "params": {},
                          "result": "found file"},
                     ])
        trace = [
            {"type": "tool_call_end", "turn": 1,
             "data": {"tool_name": "read", "result": "found file",
                      "success": True},
             "timestamp": 1.0},
        ]
        result = m.compute_sync(trace, c, judge=None)
        assert result["tool_call_result_llm"] == 0.0


class TestExecutionAccuracyMetric:
    def test_name(self):
        m = ExecutionAccuracyMetric()
        assert m.name == "execution_accuracy"

    def test_no_expected_returns_1(self):
        m = ExecutionAccuracyMetric()
        c = EvalCase(id="c0", input="hi")
        trace = [{"type": "tool_call_start", "turn": 1,
                  "data": {"tool_name": "read"}, "timestamp": 1.0}]
        result = m.compute_sync(trace, c)
        assert result["execution_accuracy"] == 1.0

    def test_exact_match(self):
        m = ExecutionAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read",
                      "arguments": '{"path": "/x"}'}, "timestamp": 1.0},
        ]
        c = EvalCase(id="c0", input="hi",
                     expected_execution=[
                         {"type": "tool", "name": "read", "params": {"path": "/x"}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["execution_accuracy"] == 1.0

    def test_partial_match(self):
        m = ExecutionAccuracyMetric()
        trace = [
            {"type": "tool_call_start", "turn": 1,
             "data": {"tool_name": "read",
                      "arguments": '{"path": "/x"}'}, "timestamp": 1.0},
        ]
        c = EvalCase(id="c0", input="hi",
                     expected_execution=[
                         {"type": "tool", "name": "read", "params": {"path": "/x"}},
                         {"type": "tool", "name": "write", "params": {"path": "/y"}},
                     ])
        result = m.compute_sync(trace, c)
        assert result["execution_accuracy"] == 0.5


class TestReasoningSimilarityMetric:
    def test_name(self):
        m = ReasoningSimilarityMetric()
        assert m.name == "reasoning_similarity"

    def test_requires_llm(self):
        m = ReasoningSimilarityMetric()
        assert m.requires_llm is True

    def test_empty_expected_returns_none(self):
        m = ReasoningSimilarityMetric()
        c = EvalCase(id="c0", input="hi")
        result = m.compute_sync([], c)
        assert result["reasoning_similarity"] is None

    def test_no_judge_returns_none(self):
        m = ReasoningSimilarityMetric()
        c = EvalCase(id="c0", input="hi",
                     expected_reasoning=["step 1"])
        result = m.compute_sync([], c)
        assert result["reasoning_similarity"] is None
