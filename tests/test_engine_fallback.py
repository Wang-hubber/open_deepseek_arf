"""Tests for GraphEngine model fallback chain and error handling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arf.engine.graph import GraphEngine


def _build_engine(**overrides):
    """Build a minimal GraphEngine with mock dependencies.

    All dependencies default to harmless mocks; pass overrides to inject
    specific behavior for the test.
    """
    defaults = {
        "loop_strategy": MagicMock(),
        "state_store": MagicMock(),
        "tool_executor": MagicMock(),
        "tool_resolver": MagicMock(),
        "error_policy": None,
        "model_router": None,
        "event_bus": None,
        "system_prompt": "",
    }
    defaults.update(overrides)
    return GraphEngine(**defaults)


class TestResolveFallback:
    """Tests for _resolve_fallback — the engine-level model failover chain."""

    def test_returns_none_when_no_error_policy(self):
        engine = _build_engine(error_policy=None, model_router=MagicMock())
        result = engine._resolve_fallback("deep", Exception("500 error"))
        assert result is None

    def test_returns_none_when_no_model_router(self):
        from arf.errors.retry import DefaultErrorPolicy
        engine = _build_engine(
            error_policy=DefaultErrorPolicy(model_5xx_action="fallback"),
            model_router=None,
        )
        result = engine._resolve_fallback("deep", Exception("500 error"))
        assert result is None

    def test_fallback_on_500_error(self):
        from arf.errors.retry import DefaultErrorPolicy
        router = MagicMock()
        router.fallback_from.return_value = "quick"

        engine = _build_engine(
            error_policy=DefaultErrorPolicy(model_5xx_action="fallback"),
            model_router=router,
        )

        result = engine._resolve_fallback("deep", Exception("HTTP 500 error"))
        assert result == "quick"
        router.fallback_from.assert_called_once_with("deep")

    def test_returns_none_when_error_policy_returns_not_fallback(self):
        from arf.errors.retry import DefaultErrorPolicy
        router = MagicMock()

        # Non-5xx errors with 5xx_action="fallback" → policy retries, not fallback
        engine = _build_engine(
            error_policy=DefaultErrorPolicy(model_5xx_action="fallback"),
            model_router=router,
        )

        result = engine._resolve_fallback("deep", Exception("timeout"))
        assert result is None
        router.fallback_from.assert_not_called()

    def test_fallback_not_in_router_returns_none(self):
        from arf.errors.retry import DefaultErrorPolicy
        router = MagicMock()
        router.fallback_from.return_value = None  # no fallback configured

        engine = _build_engine(
            error_policy=DefaultErrorPolicy(model_5xx_action="fallback"),
            model_router=router,
        )

        result = engine._resolve_fallback("deep", Exception("HTTP 503 error"))
        assert result is None


class TestInvokeFallbackChain:
    """End-to-end tests for the invoke() fallback behavior.

    These test that when the primary model fails, the engine correctly
    switches to the fallback model and continues.
    """

    def test_uses_fallback_model_on_primary_failure(self):
        """When primary model raises, engine should call fallback model."""
        from arf.errors.retry import DefaultErrorPolicy

        # Primary call raises, fallback call succeeds
        call_count = 0
        model_used = []

        def mock_call_model_sync(messages, model, **kwargs):
            nonlocal call_count
            call_count += 1
            model_used.append(model)
            if model == "deep":
                raise Exception("HTTP 500 Internal Server Error")
            return {"content": "fallback response", "tool_calls": [], "usage": {"total_tokens": 50}}

        async def mock_call_model(messages, model, **kwargs):
            return mock_call_model_sync(messages, model, **kwargs)

        loop_strategy = MagicMock()
        loop_strategy.should_continue.side_effect = [True, False]

        state_store = MagicMock()
        state_store.get = AsyncMock(return_value=None)
        state_store.put = AsyncMock()

        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        router = MagicMock()
        router.route = AsyncMock(return_value="deep")
        router.fallback_from.return_value = "quick"

        engine = _build_engine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            tool_resolver=tool_resolver,
            error_policy=DefaultErrorPolicy(model_5xx_action="fallback"),
            model_router=router,
        )
        engine.set_call_model(mock_call_model)

        state = {
            "session_id": "test",
            "messages": [{"role": "user", "content": "hello"}],
            "current_turn": 0,
            "current_model": "deep",
            "interaction_round": 0,
        }

        result = asyncio.run(engine.invoke(state))

        assert call_count == 2
        assert model_used == ["deep", "quick"]
        assert result["current_model"] == "quick"

    def test_raises_when_no_fallback_configured(self):
        """When primary fails and no fallback is available, re-raise."""
        from arf.errors.retry import DefaultErrorPolicy

        async def mock_call_model(messages, model, **kwargs):
            raise Exception("HTTP 500 Internal Server Error")

        loop_strategy = MagicMock()
        loop_strategy.should_continue.side_effect = [True, False]

        state_store = MagicMock()
        state_store.get = AsyncMock(return_value=None)
        state_store.put = AsyncMock()

        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        router = MagicMock()
        router.route = AsyncMock(return_value="deep")

        engine = _build_engine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            tool_resolver=tool_resolver,
            error_policy=DefaultErrorPolicy(model_5xx_action="fallback"),
            model_router=router,
        )
        engine.set_call_model(mock_call_model)

        state = {
            "session_id": "test",
            "messages": [{"role": "user", "content": "hello"}],
            "current_turn": 0,
            "current_model": "deep",
            "interaction_round": 0,
        }

        with pytest.raises(Exception, match="500"):
            asyncio.run(engine.invoke(state))


class TestCancelledCheck:
    def test_cancelled_returns_false_without_event(self):
        engine = _build_engine()
        assert engine._cancelled() is False

    def test_cancelled_returns_true_when_set(self):
        evt = asyncio.Event()
        engine = _build_engine()
        engine.set_cancel_event(evt)
        assert engine._cancelled() is False
        evt.set()
        assert engine._cancelled() is True
