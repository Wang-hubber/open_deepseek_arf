"""Tests for DefaultErrorPolicy — model and tool error retry/fallback behavior."""

import pytest

from arf.core.results import ErrorAction, GuardResult
from arf.core.state import TurnContext
from arf.errors.retry import DefaultErrorPolicy


class TestOnModelError:
    @pytest.fixture
    def policy(self):
        return DefaultErrorPolicy(tool_retry=2, model_5xx_action="fallback")

    def test_fallback_on_500(self, policy):
        """5xx errors should trigger fallback action when model_5xx_action='fallback'."""
        action = policy.on_model_error(
            Exception("HTTP 500 Internal Server Error"), "deep", attempt=0
        )
        assert action.action == "fallback"

    def test_fallback_on_502(self, policy):
        action = policy.on_model_error(
            Exception("HTTP 502 Bad Gateway"), "deep", attempt=0
        )
        assert action.action == "fallback"

    def test_fallback_on_503(self, policy):
        action = policy.on_model_error(
            Exception("HTTP 503 Service Unavailable"), "deep", attempt=0
        )
        assert action.action == "fallback"

    def test_fallback_on_504(self, policy):
        action = policy.on_model_error(
            Exception("HTTP 504 Gateway Timeout"), "deep", attempt=0
        )
        assert action.action == "fallback"

    def test_non_5xx_aborts_immediately(self, policy):
        """Non-5xx errors abort — protection layer handles transient retry."""
        action = policy.on_model_error(
            Exception("Connection timeout"), "deep", attempt=0
        )
        assert action.action == "abort"

    def test_non_5xx_aborts_regardless_of_attempt(self, policy):
        """Non-5xx errors abort immediately — protection layer handles retry."""
        action = policy.on_model_error(
            Exception("timeout"), "deep", attempt=3
        )
        assert action.action == "abort"
        assert "timeout" in action.message

    def test_5xx_with_retry_action_aborts(self):
        """With action='retry', engine no longer retries — protection layer handles 5xx."""
        policy = DefaultErrorPolicy(model_5xx_action="retry")
        action = policy.on_model_error(
            Exception("HTTP 500 error"), "deep", attempt=0
        )
        assert action.action == "abort"

    def test_5xx_with_abort_action_aborts(self):
        """model_5xx_action='abort' aborts immediately on 5xx."""
        policy = DefaultErrorPolicy(model_5xx_action="abort")
        action = policy.on_model_error(
            Exception("HTTP 503 error"), "deep", attempt=0
        )
        assert action.action == "abort"

    def test_5xx_detection_is_case_insensitive(self, policy):
        """5xx detection should work on message content, case-insensitively."""
        action = policy.on_model_error(
            Exception("Service returned status 503 temporarily"), "deep", attempt=0
        )
        assert action.action == "fallback"


class TestOnToolError:
    @pytest.fixture
    def policy(self):
        return DefaultErrorPolicy(tool_retry=2)

    def test_retry_with_remaining_attempts(self, policy):
        action = policy.on_tool_error(Exception("tool failed"), "file_writer", attempt=0)
        assert action.action == "retry"
        assert action.delay == pytest.approx(1.0)  # 2^0 * 1.0

    def test_retry_delay_increases(self, policy):
        a0 = policy.on_tool_error(Exception("fail"), "tool", attempt=0)
        a1 = policy.on_tool_error(Exception("fail"), "tool", attempt=1)

        assert a0.delay == pytest.approx(1.0)   # 2^0 * 1.0
        assert a1.delay == pytest.approx(2.0)   # 2^1 * 1.0

    def test_abort_after_exhausting(self, policy):
        action = policy.on_tool_error(Exception("fail"), "tool", attempt=2)
        assert action.action == "abort"
        assert "fail" in action.message


class TestOnGuardrailBlock:
    @pytest.fixture
    def ctx(self):
        return TurnContext(session_id="test", agent_name="test_agent",
                           turn=1, current_model="quick", available_models=[],
                           last_user_message="hello")

    def test_abort_by_default(self, ctx):
        policy = DefaultErrorPolicy()
        gr = GuardResult(allowed=False, reason="blocked content")
        action = policy.on_guardrail_block(gr, ctx)
        assert action.action == "abort"

    def test_ask_user_when_configured(self, ctx):
        policy = DefaultErrorPolicy(guardrail_block_action="ask_user")
        gr = GuardResult(allowed=False, reason="sensitive content")
        action = policy.on_guardrail_block(gr, ctx)
        assert action.action == "ask_user"
        assert "sensitive" in action.message
