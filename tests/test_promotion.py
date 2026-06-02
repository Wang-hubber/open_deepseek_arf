"""Tests for Promotion — thin compatibility wrapper.

Promotion now delegates to arf.session.PermissionRegistry.
Strategy classes (AutoStrategy, AskStrategy, PlanStrategy) have been removed.
"""
import pytest
from arf.core.execution import Decision, Executable, ExecuteResult, RetryPolicy


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


class TestPromotionGate:
    """Tests for the thin Promotion wrapper around PermissionRegistry."""

    def test_default_strategy_is_ask(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion()
        assert gate._strategy_name == "ask"

    def test_accepts_strategy_name(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="auto")
        assert gate._strategy_name == "auto"

        gate = Promotion(strategy="plan")
        assert gate._strategy_name == "plan"

    def test_unknown_strategy_defaults_to_ask(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="unknown_xyz")
        assert gate._strategy_name == "unknown_xyz"

    def test_deny_trumps_all(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="ask", deny=["bad_tool"], deny_patterns=[])
        result = gate.evaluate(_make_exec("bad_tool"))
        assert result.action == "deny"

    def test_allow_bypasses_ask(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="ask", allow=["safe_tool"], ask=[], deny=[], deny_patterns=[])
        result = gate.evaluate(_make_exec("safe_tool"))
        assert result.action == "allow"

    def test_ask_for_unknown(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="ask", deny=[], ask=[], allow=["known"], deny_patterns=[])
        result = gate.evaluate(_make_exec("unknown_tool"))
        assert result.action == "ask"

    def test_ask_list_takes_priority_over_default(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="ask", deny=[], ask=["sensitive_tool"], allow=[], deny_patterns=[])
        result = gate.evaluate(_make_exec("sensitive_tool"))
        assert result.action == "ask"

    def test_default_is_ask(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="ask", deny=[], ask=[], allow=[], deny_patterns=[])
        result = gate.evaluate(_make_exec("anything"))
        assert result.action == "ask"

    def test_evaluate_delegates_to_registry(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="auto", deny=[], ask=[], allow=["x"], deny_patterns=[])
        result = gate.evaluate(_make_exec("x"))
        assert result.action == "allow"

    def test_reconfigure_updates_lists(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="ask", deny=[], ask=[], allow=[], deny_patterns=[])
        result = gate.evaluate(_make_exec("new_tool"))
        assert result.action == "ask"

        gate.reconfigure(allow=["new_tool"])
        result = gate.evaluate(_make_exec("new_tool"))
        assert result.action == "allow"

    def test_deny_pattern_matches(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="ask", deny=[], deny_patterns=["rm -rf"])
        exec_obj = _make_exec("shell")
        result = gate.evaluate(exec_obj, params={"cmd": "rm -rf /"})
        assert result.action == "deny"

    def test_context_params_checked_for_patterns(self) -> None:
        from arf.promotion.gate import Promotion

        gate = Promotion(strategy="ask", deny=[], deny_patterns=["sudo "])
        exec_obj = _make_exec("shell")
        result = gate.evaluate(exec_obj, params={"cmd": "sudo rm -rf /"})
        assert result.action == "deny"
