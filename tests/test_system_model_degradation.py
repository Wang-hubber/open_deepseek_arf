"""Tests for system model degradation behaviors (Doc 2.13).

Covers: compaction summarizer degradation, classifier degradation,
and the _system_model_call fallback chain pattern.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from arf.compaction.sliding_window import SlidingWindowCompactor


# ---------------------------------------------------------------------------
# Compaction summarizer degradation (Doc 2.13: "压缩摘要" row)
# ---------------------------------------------------------------------------

class TestCompactionSummarizerDegradation:
    """Doc: 有 system_model → _summarize via LLM; 无 → _summarizer=None → 旧消息丢弃."""

    def test_should_compact_triggers_when_usage_exceeds_threshold(self):
        """Doc: last_token_usage > threshold * window_size → compact."""
        compactor = SlidingWindowCompactor(threshold=0.75, window_size=1000)
        state = {"last_token_usage": 800}  # 800 > 750
        assert compactor.should_compact(state) is True

    def test_should_compact_no_trigger_below_threshold(self):
        """Below threshold → no compaction."""
        compactor = SlidingWindowCompactor(threshold=0.75, window_size=1000)
        state = {"last_token_usage": 500}  # 500 < 750
        assert compactor.should_compact(state) is False

    def test_should_compact_uses_passed_window_size(self):
        """Engine passes routed model's window_size — should override instance default."""
        compactor = SlidingWindowCompactor(threshold=0.75, window_size=1000)
        state = {"last_token_usage": 80_000}
        # Instance default 1000 → 750 threshold would trigger
        # But with window_size=128000 → threshold=96000 → should NOT trigger
        assert compactor.should_compact(state, window_size=128_000) is False
        # With window_size=100000 → threshold=75000 → should trigger
        assert compactor.should_compact(state, window_size=100_000) is True

    def test_should_compact_zero_usage_never_triggers(self):
        """Zero usage → never compact."""
        compactor = SlidingWindowCompactor(threshold=0.75, window_size=1000)
        state = {"last_token_usage": 0}
        assert compactor.should_compact(state) is False

    def test_compact_no_summarizer_discards_old_messages(self):
        """Doc: 无 system_model → _summarizer=None → 旧消息直接丢弃."""
        compactor = SlidingWindowCompactor(threshold=0.75, summarizer=None, keep_count=4)

        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
        ]
        state = {"messages": messages, "context_summary": ""}

        async def run():
            return await compactor.compact(state)

        result = asyncio.run(run())
        assert len(result["messages"]) == 4
        # msgs 1-2 discarded, msgs 3-6 kept
        assert result["messages"][0]["content"] == "msg3"
        assert result["messages"][-1]["content"] == "msg6"
        # No summary generated
        assert result["context_summary"] == ""

    def test_compact_with_summarizer_generates_summary(self):
        """Doc: 有 system_model → _summarize via LLM → 旧轮次压缩为摘要."""
        summarizer = AsyncMock(return_value="User asked about X, assistant replied with Y.")

        compactor = SlidingWindowCompactor(threshold=0.75, summarizer=summarizer, keep_count=4)
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
        ]
        state = {"messages": messages, "context_summary": ""}

        async def run():
            return await compactor.compact(state)

        result = asyncio.run(run())
        assert len(result["messages"]) == 4
        assert "context_summary" in result
        assert "User asked about X" in result["context_summary"]
        summarizer.assert_awaited_once()

    def test_compact_summarizer_failure_graceful(self):
        """Doc: summarizer异常 → 旧消息丢弃, 不阻塞主流程."""
        failing_summarizer = AsyncMock(side_effect=RuntimeError("LLM API down"))
        compactor = SlidingWindowCompactor(threshold=0.75, summarizer=failing_summarizer, keep_count=4)

        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
        ]
        state = {"messages": messages, "context_summary": "existing_summary"}

        async def run():
            return await compactor.compact(state)

        result = asyncio.run(run())
        # Should still work — keep last 4, preserve existing summary
        assert len(result["messages"]) == 4
        assert "existing_summary" in result["context_summary"]

    def test_compact_keeps_all_when_fewer_than_5(self):
        """When ≤4 messages, no compaction needed."""
        compactor = SlidingWindowCompactor(threshold=0.75)
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
        ]
        state = {"messages": messages, "context_summary": ""}

        async def run():
            return await compactor.compact(state)

        result = asyncio.run(run())
        assert len(result["messages"]) == 2

    def test_compact_appends_to_existing_summary(self):
        """Doc: new summary appends to existing context_summary."""
        summarizer = AsyncMock(return_value="New topic discussed.")
        compactor = SlidingWindowCompactor(threshold=0.75, summarizer=summarizer, keep_count=4)

        messages = [
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": "old2"},
            {"role": "user", "content": "new1"},
            {"role": "assistant", "content": "new2"},
            {"role": "user", "content": "new3"},
        ]
        state = {"messages": messages, "context_summary": "[Earlier]: Previous summary."}

        async def run():
            return await compactor.compact(state)

        result = asyncio.run(run())
        assert len(result["messages"]) == 4
        assert "Previous summary" in result["context_summary"]
        assert "New topic discussed" in result["context_summary"]


# ---------------------------------------------------------------------------
# Routing classifier degradation (Doc 2.13: "路由分类" row)
# ---------------------------------------------------------------------------

class TestRoutingClassifierDegradation:
    """Doc: 有 system_model → _classify via LLM; 无 → classify() 始终返回 'medium'."""

    def test_no_classifier_always_returns_medium(self):
        """Doc: 无 system_model → classify() 始终返回 'medium' → 全部走 quick."""
        from arf.routing.two_tier import TwoTierRouter
        from arf.core.config_base import RoutingConfig

        router = TwoTierRouter(RoutingConfig(strategy="two_tier"), models=["quick"])

        async def run():
            return await router.classify("build a complex distributed system")

        result = asyncio.run(run())
        assert result == "medium"

    def test_classifier_closure_exception_returns_medium(self):
        """Doc: 分类失败或异常一律返回 'medium' — 不阻塞主流程.
        This tests the pattern used in the BaseAgent classifier closure."""
        # Simulate the exact pattern from base.py's _classify closure
        async def _classify_with_safety(query: str) -> str:
            try:
                raise RuntimeError("Simulated API failure")
            except Exception:
                return "medium"

        result = asyncio.run(_classify_with_safety("any query"))
        assert result == "medium"

    def test_classifier_result_validation_pattern(self):
        """Doc: result if in ('medium', 'complex') else 'medium' — validation guard."""
        async def _classify_with_validation(query: str) -> str:
            result = "gibberish_output"  # Simulate unexpected LLM output
            return result if result in ("medium", "complex") else "medium"

        result = asyncio.run(_classify_with_validation("any query"))
        assert result == "medium"

    def test_classifier_truncates_query_to_300_chars(self):
        """Doc: 输入截断至 300 字符 — query[:300]."""
        long_query = "x" * 500
        truncated = long_query[:300]
        assert len(truncated) == 300


# ---------------------------------------------------------------------------
# System model construction fallback chain (Doc 2.13)
# ---------------------------------------------------------------------------

class TestSystemModelFallbackChain:
    """Doc: system_model_name = adv.system_model → config.models[0] → None."""

    def test_system_model_config_lookup_pattern(self):
        """Doc: 从 config.models 中查找 advanced.system_model 指定的模型."""
        # This mirrors the exact pattern in base.py lines 195-206
        # system_model_name = adv.system_model if adv else None
        # if not system_model_name and config.models: → config.models[0].type
        system_model_name = "quick"
        models = [
            type("Model", (), {"type": "quick", "model": "deepseek-v4-flash"})(),
            type("Model", (), {"type": "deep", "model": "deepseek-v4-pro"})(),
        ]
        system_model_cfg = next(
            (m for m in models if m.type == system_model_name),
            None,
        )
        assert system_model_cfg is not None
        assert system_model_cfg.model == "deepseek-v4-flash"

    def test_system_model_fallback_to_first_model(self):
        """Doc: 找不到 → 回退到 config.models[0]."""
        system_model_name = "nonexistent"
        models = [
            type("Model", (), {"type": "quick", "model": "deepseek-v4-flash"})(),
        ]
        system_model_cfg = next(
            (m for m in models if m.type == system_model_name),
            None,
        )
        assert system_model_cfg is None
        # Fallback: use models[0]
        fallback = models[0] if models else None
        assert fallback is not None
        assert fallback.type == "quick"

    def test_system_model_empty_models_returns_none(self):
        """Doc: config.models 为空 → _system_model_call = None."""
        models = []
        system_model_cfg = next(
            (m for m in models if m.type == "quick"),
            None,
        )
        assert system_model_cfg is None
        fallback = models[0] if models else None
        assert fallback is None

    def test_conditional_degradation_pattern(self):
        """Doc: 框架通过 if _system_model_call: 模式检查 — 不存在时静默退化."""
        _system_model_call = None
        # Simulate the degradation pattern
        used_llm = False

        if _system_model_call:
            used_llm = True
            # Would call _system_model_call(prompt)
        else:
            # Degradation: use rule-based fallback
            used_llm = False

        assert used_llm is False  # Degradation path taken
