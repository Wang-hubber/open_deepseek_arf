"""Tests for context management behaviors described in docs/context-management.md.

Covers: CompactionStrategy Protocol, DEFAULT_WINDOW_SIZE (Doc 2.2),
summarize_tool_output (Doc 2.6), compaction edge cases (Doc 2.4),
engine integration (Doc 2.7), config defaults (Doc 2.8).
"""

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from arf.compaction.sliding_window import SlidingWindowCompactor, DEFAULT_WINDOW_SIZE


# ---------------------------------------------------------------------------
# 1. CompactionStrategy Protocol (not in doc — documentation gap)
# ---------------------------------------------------------------------------

class TestCompactionStrategyProtocol:
    """Protocol: CompactionStrategy in arf/core/protocols/compaction.py."""

    def test_protocol_has_should_compact(self):
        from arf.core.protocols.compaction import CompactionStrategy
        methods = {n for n in dir(CompactionStrategy) if not n.startswith("_")}
        assert "should_compact" in methods

    def test_protocol_has_compact(self):
        from arf.core.protocols.compaction import CompactionStrategy
        methods = {n for n in dir(CompactionStrategy) if not n.startswith("_")}
        assert "compact" in methods

    def test_should_compact_signature(self):
        """Doc: should_compact(self, state: AgentState, threshold: float = 0.75) -> bool."""
        from arf.core.protocols.compaction import CompactionStrategy
        sig = inspect.signature(CompactionStrategy.should_compact)
        params = list(sig.parameters.keys())
        assert "state" in params
        # threshold defaults to 0.75 per protocol

    def test_compact_signature(self):
        """Doc: async def compact(self, state: AgentState) -> AgentState."""
        from arf.core.protocols.compaction import CompactionStrategy
        sig = inspect.signature(CompactionStrategy.compact)
        assert "state" in sig.parameters

    def test_sliding_window_implements_protocol(self):
        """SlidingWindowCompactor structurally satisfies CompactionStrategy."""
        assert hasattr(SlidingWindowCompactor, "should_compact")
        assert hasattr(SlidingWindowCompactor, "compact")


# ---------------------------------------------------------------------------
# 2. DEFAULT_WINDOW_SIZE constant (Doc 2.2)
# ---------------------------------------------------------------------------

class TestDefaultWindowSize:
    """Doc 2.2: window_size default is 131,072 (DeepSeek V4 default context window)."""

    def test_default_window_size_is_131072(self):
        assert DEFAULT_WINDOW_SIZE == 131_072

    def test_default_window_size_used_when_not_passed(self):
        """Instance uses DEFAULT_WINDOW_SIZE when no window_size passed to constructor."""
        c = SlidingWindowCompactor()
        assert c._window_size == 131_072


# ---------------------------------------------------------------------------
# 3. summarize_tool_output (Doc 2.6)
# ---------------------------------------------------------------------------

class TestSummarizeToolOutput:
    """Doc 2.6: 工具输出超过 2000 字符时写 disk + LLM 摘要；短输出原样保留."""

    @pytest.fixture
    def tmp_workspace(self, tmp_path):
        return str(tmp_path)

    def test_short_output_returned_unchanged(self, tmp_workspace):
        """Doc: 短输出（≤2000 chars）原样保留."""
        c = SlidingWindowCompactor(workspace=tmp_workspace)
        output = "file contents: hello world"

        async def run():
            return await c.summarize_tool_output("read_file", output, 1)

        result = asyncio.run(run())
        assert result == output

    def test_output_exactly_2000_chars_returned_unchanged(self, tmp_workspace):
        """Boundary: exactly 2000 chars → still 'short', returned unchanged."""
        c = SlidingWindowCompactor(workspace=tmp_workspace)
        output = "x" * 2000

        async def run():
            return await c.summarize_tool_output("read_file", output, 1)

        result = asyncio.run(run())
        assert result == output

    def test_long_output_truncated_without_summarizer(self, tmp_workspace):
        """Doc gap: no summarizer → long output truncated + disk reference."""
        c = SlidingWindowCompactor(workspace=tmp_workspace, summarizer=None)
        output = "x" * 3000

        async def run():
            return await c.summarize_tool_output("search", output, 3)

        result = asyncio.run(run())
        assert "[Tool output truncated" in result
        assert "turn_3_search.txt" in result
        # Raw output saved to disk
        out_path = Path(tmp_workspace) / "tool_outputs" / "turn_3_search.txt"
        assert out_path.exists()
        assert len(out_path.read_text()) == 3000

    def test_long_output_summarized_with_summarizer(self, tmp_workspace):
        """Doc: long output → LLM summary + disk path reference."""
        summarizer = AsyncMock(return_value="Found 42 results matching the query.")
        c = SlidingWindowCompactor(workspace=tmp_workspace, summarizer=summarizer)
        output = "y" * 3000

        async def run():
            return await c.summarize_tool_output("search", output, 5)

        result = asyncio.run(run())
        assert "[Tool output summarized" in result
        assert "turn_5_search.txt" in result
        assert "Found 42 results" in result
        summarizer.assert_awaited_once()

    def test_long_output_falls_back_to_truncation_on_summarizer_error(self, tmp_workspace):
        """Doc gap: summarizer raises → fallback to pure truncation."""
        failing = AsyncMock(side_effect=RuntimeError("LLM down"))
        c = SlidingWindowCompactor(workspace=tmp_workspace, summarizer=failing)
        output = "z" * 3000

        async def run():
            return await c.summarize_tool_output("grep", output, 7)

        result = asyncio.run(run())
        assert "[Tool output truncated" in result
        assert "turn_7_grep.txt" in result

    def test_long_output_saves_to_correct_path(self, tmp_workspace):
        """Doc: 写入 memory/tool_outputs/turn_{N}_{tool_name}.txt."""
        c = SlidingWindowCompactor(workspace=tmp_workspace)
        output = "a" * 3000

        async def run():
            return await c.summarize_tool_output("list_files", output, 42)

        asyncio.run(run())
        out_path = Path(tmp_workspace) / "tool_outputs" / "turn_42_list_files.txt"
        assert out_path.exists()
        assert "a" * 3000 == out_path.read_text()

    def test_zeros_output_handled(self, tmp_workspace):
        """Edge case: empty output string."""
        c = SlidingWindowCompactor(workspace=tmp_workspace)
        output = ""

        async def run():
            return await c.summarize_tool_output("empty_tool", output, 1)

        result = asyncio.run(run())
        assert result == ""


# ---------------------------------------------------------------------------
# 4. Compact edge cases (Doc 2.4)
# ---------------------------------------------------------------------------

class TestCompactEdgeCases:
    """Doc 2.4: 保留最近 4 条消息，旧消息 LLM 摘要追加 [Earlier] 标记."""

    def test_compact_exactly_4_messages_no_change(self):
        """Boundary: exactly 4 messages → no compaction needed."""
        c = SlidingWindowCompactor()
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        state = {"messages": messages, "context_summary": ""}

        async def run():
            return await c.compact(state)

        result = asyncio.run(run())
        assert len(result["messages"]) == 4
        assert result["messages"] == messages  # unchanged

    def test_compact_adds_earlier_marker(self):
        """Doc: 旧消息摘要追加 [Earlier] 标记."""
        summarizer = AsyncMock(return_value="User discussed project structure.")
        c = SlidingWindowCompactor(summarizer=summarizer)
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "how are you"},
            {"role": "assistant", "content": "fine"},
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "answer"},
        ]
        state = {"messages": messages, "context_summary": ""}

        async def run():
            return await c.compact(state)

        result = asyncio.run(run())
        assert len(result["messages"]) == 4
        assert "[Earlier]" in result["context_summary"]

    def test_consecutive_compactions_accumulate(self):
        """Doc: 摘要叠加而非覆盖 — 连续多轮压缩时累积保留."""
        summarizer = AsyncMock()
        summarizer.side_effect = ["First summary.", "Second summary."]
        c = SlidingWindowCompactor(summarizer=summarizer)

        # First compaction
        messages = [
            {"role": "user", "content": f"msg{i}"} for i in range(6)
        ]
        state = {"messages": messages, "context_summary": ""}

        async def run():
            s1 = await c.compact(state.copy())
            # Simulate new messages added after first compaction
            new_msgs = s1["messages"] + [
                {"role": "user", "content": "msg6"},
                {"role": "assistant", "content": "msg7"},
            ]
            s2 = {"messages": new_msgs, "context_summary": s1["context_summary"]}
            return await c.compact(s2)

        result = asyncio.run(run())
        assert "First summary" in result["context_summary"]
        assert "Second summary" in result["context_summary"]
        assert summarizer.call_count == 2

    def test_should_compact_with_explicit_threshold_override(self):
        """Doc 2.2: should_compact supports per-call threshold override."""
        c = SlidingWindowCompactor(threshold=0.75, window_size=1000)
        state = {"last_token_usage": 600}
        # Instance threshold 0.75 → limit 750 → 600 would NOT trigger
        assert c.should_compact(state) is False
        # Override threshold 0.5 → limit 500 → 600 WOULD trigger
        assert c.should_compact(state, threshold=0.5) is True

    def test_engine_uses_routed_model_window_size(self):
        """Doc 2.3: 引擎将当前选定模型的窗口大小传入 should_compact."""
        c = SlidingWindowCompactor(threshold=0.75, window_size=100_000)
        state = {"last_token_usage": 500_000}
        # Instance default 100_000 → limit 75_000 → 500k WOULD trigger
        assert c.should_compact(state) is True
        # deep model with 1M window → limit 750_000 → 500k would NOT trigger
        assert c.should_compact(state, window_size=1_000_000) is False


# ---------------------------------------------------------------------------
# 5. CompactionConfig defaults (Doc 2.8)
# ---------------------------------------------------------------------------

class TestCompactionConfigDefaults:
    """Doc 2.8: compaction config in agent.yaml."""

    def test_default_strategy_is_sliding_window(self):
        from arf.core.config_base import CompactionConfig
        c = CompactionConfig()
        assert c.strategy == "sliding_window"

    def test_default_threshold_is_0_75(self):
        from arf.core.config_base import CompactionConfig
        c = CompactionConfig()
        assert c.threshold == 0.75

    def test_threshold_bounds(self):
        """Doc: threshold range 0.0-1.0."""
        from arf.core.config_base import CompactionConfig
        c = CompactionConfig(threshold=0.5)
        assert c.threshold == 0.5
        c2 = CompactionConfig(threshold=1.0)
        assert c2.threshold == 1.0
        with pytest.raises(Exception):
            CompactionConfig(threshold=1.5)  # > 1.0


# ---------------------------------------------------------------------------
# 6. Engine Integration (Doc 2.7)
# ---------------------------------------------------------------------------

class TestEngineIntegration:
    """Doc 2.7: compaction in GraphEngine.invoke() and astream()."""

    def test_compaction_in_invoke_method(self):
        """Doc: compaction.should_compact() called in GraphEngine.invoke()."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "compaction.should_compact" in src
        assert "compaction.compact" in src

    def test_compaction_in_astream_method(self):
        """Doc: compaction.should_compact() also called in astream()."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.astream)
        assert "compaction.should_compact" in src
        assert "compaction.compact" in src

    def test_compaction_after_routing_order_invoke(self):
        """Doc: 路由之后、模型调用之前 — routing before compaction."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        route_pos = src.find("model_router.route")
        compact_pos = src.find("compaction.should_compact")
        model_call_pos = src.find("self._call_model")
        assert route_pos < compact_pos < model_call_pos, (
            f"Expected routing({route_pos}) < compaction({compact_pos}) < model_call({model_call_pos})"
        )

    def test_compaction_after_routing_order_astream(self):
        """Doc: same ordering in astream()."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.astream)
        route_pos = src.find("model_router.route")
        compact_pos = src.find("compaction.should_compact")
        assert route_pos > 0 and compact_pos > 0
        assert route_pos < compact_pos, (
            f"Routing({route_pos}) must come before compaction({compact_pos}) in astream"
        )

    def test_tool_output_summarization_called_after_success(self):
        """Doc: 工具执行成功后调用 compaction.summarize_tool_output()."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "summarize_tool_output" in src

    def test_engine_guards_compaction_with_none_check(self):
        """Engine checks `if self.compaction:` before calling compaction methods."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "if self.compaction" in src

    def test_window_size_from_model_windows(self):
        """Doc 2.3: 引擎从 _model_windows[model] 获取窗口大小."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "_model_windows" in src

    def test_fallback_window_size_uses_default_constant(self):
        """When _model_windows is missing, engine falls back to DEFAULT_WINDOW_SIZE."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "DEFAULT_WINDOW_SIZE" in src, (
            "Engine should use DEFAULT_WINDOW_SIZE constant as fallback"
        )

    def test_compaction_emits_events(self):
        """Doc: engine emits compaction_start and compaction_end events."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert '"compaction_start"' in src
        assert '"compaction_end"' in src

    def test_context_summary_propagated_to_system_prompt(self):
        """Doc: context_summary 通过 {{MEMORY}} 注入 system prompt."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "context_summary" in src

    def test_last_token_usage_updated_after_model_call(self):
        """Doc: 以上一轮模型调用返回的 usage.total_tokens 为信号."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "last_token_usage" in src
        assert "total_tokens" in src


# ---------------------------------------------------------------------------
# 7. LLM Summarizer structure (Doc 2.5)
# ---------------------------------------------------------------------------

class TestSummarizerStructure:
    """Doc 2.5: summarizer closure in BaseAgent.__init__, 7-dimension output."""

    def test_summarizer_uses_system_model(self):
        """Doc: _summarize 复用 system model."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        summarizer_block = src.split("_summarize")[1][:300] if "_summarize" in src else ""
        assert "_system_model_call" in summarizer_block or "_system_model_call" in src

    def test_summarizer_truncates_messages_last_30_at_300_chars(self):
        """Doc: 取最近 30 条旧消息，每条截断至 300 字符."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        summarizer_block = src.split("_summarize")[1][:500] if "_summarize" in src else ""
        assert "[-30:]" in summarizer_block or "[-30:]" in src
        assert "[:300]" in summarizer_block or "[:300]" in src

    def test_summarizer_has_7_sections(self):
        """Doc: 7 sections — Completed, In Progress, Files Modified, Decisions,
        Facts & Preferences, Errors & Debugging, Next Steps."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        sections = [
            "Completed", "In Progress", "Files Modified", "Decisions",
            "Facts & Preferences", "Errors & Debugging", "Next Steps",
        ]
        for s in sections:
            assert s in src, f"Summarizer section '{s}' not found in BaseAgent.__init__"

    def test_summarizer_returns_conversation_summary_unavailable_on_exception(self):
        """Doc gap: summarizer exception → '(conversation summary unavailable)'."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "conversation summary unavailable" in src

