"""Tests for Promotion — permission gating layer."""

import pytest
from arf.core.execution import Decision, Executable, ExecuteResult, RetryPolicy
from arf.promotion.strategies import AutoStrategy, AskStrategy, PlanStrategy


def _make_exec(
    name: str, kind: str = "tool", side_effect: bool = True, **kwargs
) -> Executable:
    deps = kwargs.get("dependencies", [])
    res = kwargs.get("resources", [])

    class _E:
        async def execute(self) -> ExecuteResult:
            return ExecuteResult(name=self.name, success=True)

        async def rollback(self) -> None:
            pass

    _E.name = name
    _E.kind = kind
    _E.dependencies = deps
    _E.resources = res
    _E.side_effect = side_effect
    _E.retry_policy = RetryPolicy()
    _E.timeout = None
    return _E()  # type: ignore[return-value]


class TestAutoStrategy:
    def test_all_allowed(self) -> None:
        s = AutoStrategy()
        assert s.evaluate(_make_exec("a")).action == "allow"
        assert s.evaluate(_make_exec("b")).action == "allow"
        assert s.evaluate(_make_exec("dangerous")).action == "allow"


class TestAskStrategy:
    def test_deny_trumps_all(self) -> None:
        s = AskStrategy(deny=["bad_tool"], deny_patterns=[])
        assert s.evaluate(_make_exec("bad_tool")).action == "deny"

    def test_deny_pattern_matches(self) -> None:
        s = AskStrategy(deny=[], deny_patterns=["rm -rf"])
        exec_obj = _make_exec("shell")
        result = s.evaluate(exec_obj, params={"cmd": "rm -rf /"})
        assert result.action == "deny"

    def test_allow_bypasses_ask(self) -> None:
        s = AskStrategy(
            allow=["safe_tool"], ask=[], deny=[], deny_patterns=[]
        )
        assert s.evaluate(_make_exec("safe_tool")).action == "allow"

    def test_ask_for_unknown(self) -> None:
        s = AskStrategy(
            deny=[], ask=[], allow=["known"], deny_patterns=[]
        )
        assert s.evaluate(_make_exec("unknown_tool")).action == "ask"

    def test_ask_list_takes_priority_over_default(self) -> None:
        s = AskStrategy(
            deny=[], ask=["sensitive_tool"], allow=[], deny_patterns=[]
        )
        assert s.evaluate(_make_exec("sensitive_tool")).action == "ask"

    def test_default_is_ask(self) -> None:
        s = AskStrategy(deny=[], ask=[], allow=[], deny_patterns=[])
        assert s.evaluate(_make_exec("anything")).action == "ask"

    def test_context_params_checked_for_patterns(self) -> None:
        s = AskStrategy(deny=[], deny_patterns=["sudo "])
        exec_obj = _make_exec("shell")
        result = s.evaluate(exec_obj, params={"cmd": "sudo rm -rf /"})
        assert result.action == "deny"


class TestPlanStrategy:
    def test_side_effect_true_is_denied(self) -> None:
        s = PlanStrategy()
        result = s.evaluate(_make_exec("file_writer", side_effect=True))
        assert result.action == "deny"
        assert "只读" in result.reason

    def test_side_effect_false_is_allowed(self) -> None:
        s = PlanStrategy()
        result = s.evaluate(_make_exec("file_reader", side_effect=False))
        assert result.action == "allow"

    def test_model_call_always_allowed(self) -> None:
        s = PlanStrategy()
        result = s.evaluate(
            _make_exec("model", kind="model_call", side_effect=False)
        )
        assert result.action == "allow"

    def test_hook_with_side_effect_is_denied(self) -> None:
        s = PlanStrategy()
        result = s.evaluate(_make_exec("hook", kind="hook", side_effect=True))
        assert result.action == "deny"


class TestPromotionGate:
    def test_select_auto_strategy(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="auto")
        assert isinstance(gate._strategy, AutoStrategy)

    def test_select_ask_strategy(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="ask", deny=["bad"], ask=[], allow=["ok"])
        assert isinstance(gate._strategy, AskStrategy)
        assert gate._strategy.deny_list == {"bad"}

    def test_select_plan_strategy(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="plan")
        assert isinstance(gate._strategy, PlanStrategy)

    def test_evaluate_delegates_to_strategy(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="auto")
        result = gate.evaluate(_make_exec("x"))
        assert result.action == "allow"

    def test_unknown_strategy_defaults_to_ask(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="unknown_xyz")
        assert isinstance(gate._strategy, AskStrategy)
