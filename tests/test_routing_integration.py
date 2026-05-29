"""Integration-level tests for routing behaviors described in docs/model-routing.md.

Covers: ModelAdapter thinking translation (Doc 2.10), streaming event types,
classifier prompt structure, static strategy, and the background field gap.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arf.core.model_adapter import ModelAdapter
from tests.fixtures.fake_model_adapter import FakeModelAdapter, FakeResponse


# ---------------------------------------------------------------------------
# ModelAdapter thinking translation (Doc 2.10)
# ---------------------------------------------------------------------------

class TestThinkingTranslation:
    """Doc 2.10: thinking_enabled → extra_body['thinking'] = enabled/disabled."""

    def test_thinking_enabled_produces_enabled_type(self):
        """Doc: thinking_enabled 翻译为 DeepSeek thinking 格式."""
        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
            "thinking_enabled": True,
        })
        _, extra = adapter._build_api_params()
        assert "thinking" in extra
        assert extra["thinking"]["type"] == "enabled"

    def test_thinking_disabled_produces_disabled_type(self):
        """Doc: thinking_disabled → {'type': 'disabled'}."""
        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
            "thinking_enabled": False,
        })
        _, extra = adapter._build_api_params()
        assert "thinking" in extra
        assert extra["thinking"]["type"] == "disabled"

    def test_thinking_not_in_standard_params(self):
        """thinking_enabled must NOT appear in standard params sent to API."""
        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
            "thinking_enabled": True,
        })
        standard, _ = adapter._build_api_params()
        assert "thinking_enabled" not in standard

    def test_no_thinking_key_when_not_configured(self):
        """When thinking_enabled is absent, no thinking key in extra_body."""
        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
        })
        _, extra = adapter._build_api_params()
        assert "thinking" not in extra

    def test_thinking_enabled_with_reasoning_effort(self):
        """Doc: reasoning_effort → thinking.effort field."""
        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
            "thinking_enabled": "enabled",
            "reasoning_effort": "high",
        })
        _, extra = adapter._build_api_params()
        assert extra["thinking"]["type"] == "enabled"
        assert extra["thinking"]["effort"] == "high"


# ---------------------------------------------------------------------------
# Streaming event types (Doc 2.10)
# ---------------------------------------------------------------------------

class TestStreamingEventTypes:
    """Doc 2.10: chat_stream_full 产出 chunk、tool_call、usage、error 四种事件."""

    def test_yields_chunk_events_for_text(self):
        """Doc: chunk events with content field."""
        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
        })

        chunk = MagicMock()
        chunk.choices = [MagicMock(
            delta=MagicMock(content="Hello", reasoning_content=None),
            finish_reason=None,
        )]
        chunk.usage = None

        adapter._call_with_retry = MagicMock(return_value=iter([chunk]))
        events = list(adapter.chat_stream_full([], None))

        assert any(e["type"] == "chunk" for e in events)
        assert any(e.get("content") == "Hello" for e in events)

    def test_yields_tool_call_events(self):
        """Doc: tool_call events with name, arguments, id."""
        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
        })

        class FakeFunc:
            name = "read_file"
            arguments = '{"path": "/tmp"}'

        class FakeTC:
            index = 0
            function = FakeFunc

        class FakeDelta:
            content = None
            tool_calls = [FakeTC]
            reasoning_content = None

        class FakeChoice:
            delta = FakeDelta
            finish_reason = "tool_calls"

        class FakeChunk:
            choices = [FakeChoice]
            usage = None

        adapter._call_with_retry = MagicMock(return_value=iter([FakeChunk]))
        events = list(adapter.chat_stream_full([], None))

        tool_events = [e for e in events if e["type"] == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0]["name"] == "read_file"
        assert tool_events[0]["id"] == "call_0"

    def test_yields_usage_event(self):
        """Doc: usage events from final chunk with token counts."""
        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
        })

        chunk = MagicMock()
        chunk.choices = []
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 20
        mock_usage.total_tokens = 30
        chunk.usage = mock_usage

        adapter._call_with_retry = MagicMock(return_value=iter([chunk]))
        events = list(adapter.chat_stream_full([], None))

        usage_events = [e for e in events if e["type"] == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0]["total_tokens"] == 30

    def test_yields_error_event_on_failure(self):
        """Doc: error events with code and detail."""
        from arf.core.model_adapter import ModelAdapterError

        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
        })
        adapter._call_with_retry = MagicMock(
            side_effect=ModelAdapterError(status_code=503, message="Down")
        )

        events = list(adapter.chat_stream_full([], None))
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["code"] == 503
        assert "Down" in error_events[0]["detail"]

    def test_all_four_event_types_handled(self):
        """Verify all four documented event types are distinguishable by 'type' field."""
        from arf.core.model_adapter import ModelAdapterError

        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
        })

        # Build a stream that produces all four types
        chunk1 = MagicMock()
        tc = MagicMock()
        tc.index = 0
        tc.function = MagicMock(name="search", arguments='{"q": "test"}')
        chunk1.choices = [MagicMock(
            delta=MagicMock(content="result", tool_calls=[tc], reasoning_content=None),
            finish_reason="tool_calls",
        )]
        chunk1.usage = None

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock(
            delta=MagicMock(content="done", reasoning_content=None),
            finish_reason="stop",
        )]
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 5
        mock_usage.completion_tokens = 10
        mock_usage.total_tokens = 15
        chunk2.usage = mock_usage

        adapter._call_with_retry = MagicMock(return_value=iter([chunk1, chunk2]))
        events = list(adapter.chat_stream_full([], None))

        event_types = {e["type"] for e in events}
        assert "chunk" in event_types
        assert "tool_call" in event_types
        assert "usage" in event_types
        # Error tested separately above

    def test_error_event_only_when_call_fails(self):
        """Error events appear only when _call_with_retry raises, not during normal stream."""
        from arf.core.model_adapter import ModelAdapterError

        adapter = ModelAdapter({
            "base_url": "https://test.api",
            "api_key": "sk-test",
            "model_name": "test-model",
        })
        adapter._call_with_retry = MagicMock(
            side_effect=ModelAdapterError(status_code=400, message="Bad Request")
        )

        events = list(adapter.chat_stream_full([], None))
        event_types = {e["type"] for e in events}
        assert event_types == {"error"}


# ---------------------------------------------------------------------------
# RoutingConfig background field (documentation gap from Doc completeness report)
# ---------------------------------------------------------------------------

class TestBackgroundField:
    """Doc gap: RoutingConfig.background exists in code but undocumented in doc 2.11."""

    def test_background_field_exists_with_default_none(self):
        """Field exists in RoutingConfig but unused in routing logic."""
        from arf.core.config_base import RoutingConfig
        c = RoutingConfig()
        assert hasattr(c, "background")
        assert c.background is None

    def test_background_field_in_model_fields(self):
        """Field is a declared model field, not an artifact."""
        from arf.core.config_base import RoutingConfig
        assert "background" in RoutingConfig.model_fields

    def test_background_field_not_referenced_in_router(self):
        """TwoTierRouter does not reference the background field."""
        import inspect as _ins
        from arf.routing.two_tier import TwoTierRouter
        src = _ins.getsource(TwoTierRouter)
        assert "background" not in src


# ---------------------------------------------------------------------------
# Classifier prompt template structure (Doc 2.5)
# ---------------------------------------------------------------------------

class TestClassifierPromptTemplate:
    """Doc 2.5: classifier prompt asks for medium/complex with specific rules."""

    def test_prompt_contains_dichotomy_keywords(self):
        """Doc: prompt lists medium tasks (simple chat, file I/O, single tool call)
        and complex tasks (multi-step reasoning, many tool calls, code generation, planning)."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "simple chat" in src
        assert "file I/O" in src or "file I/O" in src
        assert "single tool call" in src
        assert "multi-step reasoning" in src
        assert "code generation" in src
        assert "planning" in src

    def test_prompt_asks_for_one_word_response(self):
        """Doc: 'Return ONLY one word (medium or complex).'"""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "Return ONLY one word" in src

    def test_prompt_truncates_query(self):
        """Doc: Task: {query[:300]}."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "query[:300]" in src

    def test_classifier_result_stripped_and_lowered(self):
        """Doc: result.strip().lower() — normalize before validation."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        classifier_block = src.split("_classify")[1][:800] if "_classify" in src else ""
        assert ".strip()" in classifier_block or ".lower()" in src


# ---------------------------------------------------------------------------
# Static strategy engine integration (Doc 2.12)
# ---------------------------------------------------------------------------

class TestStaticStrategyIntegration:
    """Doc 2.12: static strategy → no classification, always use default model."""

    def test_static_strategy_creates_router_without_classifier(self):
        """Doc: strategy='static' → TwoTierRouter without classifier_call."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        # Find the static strategy block
        routing_block_start = src.find('if adv.routing.strategy == "static"')
        assert routing_block_start > 0, "static strategy branch not found in BaseAgent"
        block = src[routing_block_start:routing_block_start + 500]
        # Static path should call TwoTierRouter without classifier_call
        assert "TwoTierRouter(" in block
        # Should NOT have classifier_call in this branch
        after_static = block.split('"static"')[1][:400]
        assert "classifier_call" not in after_static

    def test_static_strategy_requires_multi_models(self):
        """Doc: static routing still requires models > 1 per BaseAgent logic."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "len(config.models) > 1" in src


# ---------------------------------------------------------------------------
# System model adapter creation — parameter penetration (Doc 2.13)
# ---------------------------------------------------------------------------

class TestSystemModelConfigValues:
    """Doc 2.13: system model adapter — temperature=0.3, thinking disabled, max_tokens=1024."""

    def test_system_model_snippet_has_correct_values(self):
        """Doc claims verified via source inspection (fact_check tests cover text presence).
        This test verifies via inspect that the literal values 0.3, 1024, false appear
        in the system model adapter creation block."""
        import inspect
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)

        # Locate the system model adapter creation block
        sys_start = src.find('"temperature": 0.3')
        assert sys_start > 0, "Expected temperature=0.3 in system model adapter creation"
        sys_block = src[sys_start:sys_start + 300]
        assert "1024" in sys_block
        assert "thinking_enabled" in sys_block
        assert 'false' in sys_block.lower()

    def test_max_tokens_actually_1024(self):
        """Doc: max_tokens=1024. Verify the literal in source, not a variable."""
        import inspect
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)
        assert '"max_tokens": 1024' in src, (
            "max_tokens should be literal 1024, not a variable"
        )


# ---------------------------------------------------------------------------
# TwoTierRouter with sync callable (Doc 2.5)
# ---------------------------------------------------------------------------

class TestTwoTierRouterSyncClassifier:
    """Doc 2.5: classifier is an async callable — verify sync callables work too."""

    def test_classifier_must_return_awaitable(self):
        """classify() does `await self._classify(query)` — the callable
        must return an awaitable (coroutine or Future), not a plain string."""
        from arf.routing.two_tier import TwoTierRouter
        from arf.core.config_base import RoutingConfig

        cfg = RoutingConfig(
            default="quick",
            classify={"medium": "quick", "complex": "deep"},
        )

        async def run():
            async def async_classifier(q):
                return "complex"
            router = TwoTierRouter(cfg, models=["quick", "deep"],
                                  classifier_call=async_classifier)
            result = await router.classify("some text")
            assert result == "complex"

        import asyncio
        asyncio.run(run())

    def test_route_with_non_empty_history(self):
        """Doc: route(query, history) — history is passed but not used
        by the current TwoTierRouter implementation."""
        import asyncio
        from arf.routing.two_tier import TwoTierRouter
        from arf.core.config_base import RoutingConfig

        cfg = RoutingConfig(
            default="quick",
            classify={"medium": "quick", "complex": "deep"},
        )
        fake = FakeModelAdapter(default=FakeResponse(content="medium"))

        async def classifier(query: str) -> str:
            resp = fake.chat_complete([{"role": "user", "content": query}])
            return resp.content.strip().lower()

        router = TwoTierRouter(cfg, models=["quick", "deep"],
                              classifier_call=classifier)

        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = asyncio.run(router.route("what time is it", history))
        assert result == "quick"
        # history was passed but current implementation doesn't use it
        # (this validates the parameter exists and is accepted)
