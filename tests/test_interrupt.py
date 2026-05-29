"""Tests for interrupt/error handling behaviors described in docs/interrupt.md.

Covers: ErrorPolicy Protocol, ErrorConfig defaults, DefaultErrorPolicy guardrail
behavior, cancel event edge cases. Supplements the extensive existing fact_check
and unit tests.
"""

import asyncio
import inspect

import pytest


# ---------------------------------------------------------------------------
# 1. ErrorPolicy Protocol (not in doc — documentation gap)
# ---------------------------------------------------------------------------

class TestErrorPolicyProtocol:
    """Protocol: ErrorPolicy in arf/core/protocols/errors.py."""

    def test_protocol_has_on_tool_error(self):
        from arf.core.protocols.errors import ErrorPolicy
        methods = {n for n in dir(ErrorPolicy) if not n.startswith("_")}
        assert "on_tool_error" in methods

    def test_protocol_has_on_model_error(self):
        from arf.core.protocols.errors import ErrorPolicy
        methods = {n for n in dir(ErrorPolicy) if not n.startswith("_")}
        assert "on_model_error" in methods

    def test_protocol_has_on_guardrail_block(self):
        from arf.core.protocols.errors import ErrorPolicy
        methods = {n for n in dir(ErrorPolicy) if not n.startswith("_")}
        assert "on_guardrail_block" in methods

    def test_on_tool_error_signature(self):
        """Doc: on_tool_error(error, tool_name, attempt) -> ErrorAction."""
        from arf.core.protocols.errors import ErrorPolicy
        sig = inspect.signature(ErrorPolicy.on_tool_error)
        params = list(sig.parameters.keys())
        assert "error" in params
        assert "tool_name" in params
        assert "attempt" in params

    def test_on_model_error_signature(self):
        """Doc: on_model_error(error, model_name, attempt) -> ErrorAction."""
        from arf.core.protocols.errors import ErrorPolicy
        sig = inspect.signature(ErrorPolicy.on_model_error)
        params = list(sig.parameters.keys())
        assert "error" in params
        assert "model_name" in params
        assert "attempt" in params

    def test_on_guardrail_block_signature(self):
        """Doc: on_guardrail_block(result, context) -> ErrorAction."""
        from arf.core.protocols.errors import ErrorPolicy
        sig = inspect.signature(ErrorPolicy.on_guardrail_block)
        params = list(sig.parameters.keys())
        assert "result" in params
        assert "context" in params

    def test_default_error_policy_satisfies_protocol(self):
        """DefaultErrorPolicy structurally satisfies ErrorPolicy."""
        from arf.errors.retry import DefaultErrorPolicy
        assert hasattr(DefaultErrorPolicy, "on_tool_error")
        assert hasattr(DefaultErrorPolicy, "on_model_error")
        assert hasattr(DefaultErrorPolicy, "on_guardrail_block")


# ---------------------------------------------------------------------------
# 2. ErrorConfig defaults (Doc gap: Section 4)
# ---------------------------------------------------------------------------

class TestErrorConfigDefaults:
    """Doc: ErrorConfig in arf/core/config_base.py — not in doc Section 4."""

    def test_default_tool_retry_is_2(self):
        from arf.core.config_base import ErrorConfig
        c = ErrorConfig()
        assert c.tool_retry == 2

    def test_default_model_5xx_action_is_fallback(self):
        from arf.core.config_base import ErrorConfig
        c = ErrorConfig()
        assert c.model_5xx_action == "fallback"

    def test_default_guardrail_block_action_is_abort(self):
        from arf.core.config_base import ErrorConfig
        c = ErrorConfig()
        assert c.guardrail_block_action == "abort"

    def test_valid_actions_for_model_5xx(self):
        """Doc: model_5xx_action accepts fallback, retry, abort."""
        from arf.core.config_base import ErrorConfig
        for action in ("fallback", "retry", "abort"):
            c = ErrorConfig(model_5xx_action=action)
            assert c.model_5xx_action == action

    def test_valid_actions_for_guardrail_block(self):
        """Doc: guardrail_block_action accepts abort, ask_user."""
        from arf.core.config_base import ErrorConfig
        for action in ("abort", "ask_user"):
            c = ErrorConfig(guardrail_block_action=action)
            assert c.guardrail_block_action == action


# ---------------------------------------------------------------------------
# 3. DefaultErrorPolicy guardrail behavior (Doc gap)
# ---------------------------------------------------------------------------

class TestGuardrailBlockBehavior:
    """Doc: DefaultErrorPolicy.on_guardrail_block — abort vs ask_user."""

    def test_guardrail_abort_action(self):
        from arf.errors.retry import DefaultErrorPolicy
        from arf.core.results import GuardResult
        from arf.core.state import TurnContext

        policy = DefaultErrorPolicy(guardrail_block_action="abort")
        result = GuardResult(allowed=False, reason="path traversal detected")
        ctx = TurnContext(
            session_id="s1", agent_name="main", turn=1,
            current_model="deepseek", available_models=["deepseek"],
            last_user_message="test", last_tool_calls=[],
        )
        action = policy.on_guardrail_block(result, ctx)
        assert action.action == "abort"
        assert "path traversal" in action.message

    def test_guardrail_ask_user_action(self):
        from arf.errors.retry import DefaultErrorPolicy
        from arf.core.results import GuardResult
        from arf.core.state import TurnContext

        policy = DefaultErrorPolicy(guardrail_block_action="ask_user")
        result = GuardResult(allowed=False, reason="permission required")
        ctx = TurnContext(
            session_id="s1", agent_name="main", turn=1,
            current_model="deepseek", available_models=["deepseek"],
            last_user_message="test", last_tool_calls=[],
        )
        action = policy.on_guardrail_block(result, ctx)
        assert action.action == "ask_user"
        assert "permission required" in action.message

    def test_default_is_abort(self):
        from arf.errors.retry import DefaultErrorPolicy
        from arf.core.results import GuardResult
        from arf.core.state import TurnContext

        policy = DefaultErrorPolicy()
        result = GuardResult(allowed=False, reason="blocked")
        ctx = TurnContext(
            session_id="s1", agent_name="main", turn=1,
            current_model="deepseek", available_models=["deepseek"],
            last_user_message="test", last_tool_calls=[],
        )
        action = policy.on_guardrail_block(result, ctx)
        assert action.action == "abort"

    def test_guard_result_has_allowed_reason_fields(self):
        """GuardResult is a dataclass with allowed, reason, modified_message."""
        from arf.core.results import GuardResult
        from dataclasses import fields
        names = {f.name for f in fields(GuardResult)}
        for field_name in ("allowed", "reason", "modified_message"):
            assert field_name in names, f"Missing field: {field_name}"

    def test_guard_result_defaults(self):
        from arf.core.results import GuardResult
        g = GuardResult(allowed=True)
        assert g.allowed is True
        assert g.reason == ""
        assert g.modified_message is None


# ---------------------------------------------------------------------------
# 4. Tool retry exponential backoff (Doc gap)
# ---------------------------------------------------------------------------

class TestToolRetryBackoff:
    """Doc: DefaultErrorPolicy.on_tool_error uses exponential backoff."""

    def test_retry_delay_doubles_each_attempt(self):
        from arf.errors.retry import DefaultErrorPolicy

        policy = DefaultErrorPolicy(tool_retry=3)
        delays = []
        for attempt in range(3):
            action = policy.on_tool_error(RuntimeError("e"), "tool", attempt)
            if action.action == "retry":
                delays.append(action.delay)

        # 2^0 * 1.0 = 1.0, 2^1 * 1.0 = 2.0, 2^2 * 1.0 = 4.0
        assert delays == pytest.approx([1.0, 2.0, 4.0], rel=1e-9)

    def test_retry_exhausted_returns_abort(self):
        from arf.errors.retry import DefaultErrorPolicy

        policy = DefaultErrorPolicy(tool_retry=1)
        # attempt 0 < tool_retry=1 → retry
        a1 = policy.on_tool_error(RuntimeError("e"), "tool", 0)
        assert a1.action == "retry"
        # attempt 1 >= tool_retry=1 → abort
        a2 = policy.on_tool_error(RuntimeError("e"), "tool", 1)
        assert a2.action == "abort"
        assert "e" in a2.message


# ---------------------------------------------------------------------------
# 5. on_model_error — abort action (Doc gap)
# ---------------------------------------------------------------------------

class TestModelErrorAbort:
    """Doc: on_model_error with model_5xx_action=abort returns abort."""

    def test_5xx_with_abort_returns_abort(self):
        from arf.errors.retry import DefaultErrorPolicy
        policy = DefaultErrorPolicy(model_5xx_action="abort")
        action = policy.on_model_error(RuntimeError("503 service unavailable"), "deep", 0)
        assert action.action == "abort"

    def test_5xx_with_retry_returns_abort(self):
        """model_5xx_action=retry still returns abort — engine-level retry
        is removed; retry is handled by protection layer."""
        from arf.errors.retry import DefaultErrorPolicy
        policy = DefaultErrorPolicy(model_5xx_action="retry")
        action = policy.on_model_error(RuntimeError("500 error"), "deep", 0)
        assert action.action == "abort"

    def test_non_5xx_error_returns_abort(self):
        """Non-5xx errors always abort regardless of model_5xx_action."""
        from arf.errors.retry import DefaultErrorPolicy
        policy = DefaultErrorPolicy(model_5xx_action="fallback")
        action = policy.on_model_error(RuntimeError("400 bad request"), "deep", 0)
        assert action.action == "abort"

    def test_5xx_detection_matches_all_variants(self):
        """Doc: 5xx detection matches 500, 502, 503, 504 in error message."""
        from arf.errors.retry import DefaultErrorPolicy
        policy = DefaultErrorPolicy(model_5xx_action="fallback")
        for status in (500, 502, 503, 504):
            action = policy.on_model_error(
                RuntimeError(f"HTTP {status} error"), "deep", 0
            )
            assert action.action == "fallback", f"Status {status} should trigger fallback"
