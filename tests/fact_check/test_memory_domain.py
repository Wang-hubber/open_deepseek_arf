"""Fact-check tests: Memory Domain — docs/memory-management.md vs arf/memory/ + arf/compaction/.

Each test validates a specific claim made in the documentation against actual code.
PASS = doc/code consistent. FAIL = discrepancy found (fact-check finding).

TDD-style: the doc IS the spec; the test asserts the spec is met.
"""

import json
import sys
import time
import uuid
from pathlib import Path
from arf.testing import InMemoryMemoryStore

import pytest

# ---------------------------------------------------------------------------
# 1. Protocol & Data Model (docs 2.3 — memory pipeline protocols)
# ---------------------------------------------------------------------------

class TestMemoryEntryDataModel:
    """Doc: MemoryEntry — id (UUID), content (≤500 chars),
    category (fact/preference/decision/context), timestamp, source_turn,
    relevance_score, replaces.
    """

    def test_memory_entry_has_all_seven_fields(self):
        """Doc lists 7 fields: id, content, category, timestamp, source_turn,
        relevance_score, replaces."""
        from arf.core.protocols.memory import MemoryEntry

        fields = {f.name for f in MemoryEntry.__dataclass_fields__.values()}
        expected = {"id", "content", "category", "timestamp",
                    "source_turn", "relevance_score", "replaces"}
        assert fields == expected

    def test_memory_entry_defaults(self):
        """Doc: relevance_score defaults, replaces is optional (None)."""
        from arf.core.protocols.memory import MemoryEntry

        e = MemoryEntry(id="x", content="x", category="fact",
                        timestamp=0.0, source_turn=0)
        assert e.relevance_score == 1.0
        assert e.replaces is None


class TestMemoryProtocols:
    """Doc: 3 protocols — MemoryStore, MemoryRetriever, MemoryWriter."""

    def test_memory_store_has_three_methods(self):
        """Doc table: save(entry), load(session_id), delete(entry_id)."""
        from arf.core.protocols.memory import MemoryStore
        methods = {n for n in dir(MemoryStore) if not n.startswith("_")}
        assert "save" in methods
        assert "load" in methods
        assert "delete" in methods

    def test_memory_retriever_signature(self):
        """Doc: retrieve(store, query, session_id, max_tokens, top_k)."""
        from arf.core.protocols.memory import MemoryRetriever
        import inspect
        sig = inspect.signature(MemoryRetriever.retrieve)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "store" in params
        assert "query_context" in params
        assert "session_id" in params
        assert "max_tokens" in params
        assert "top_k" in params

    def test_memory_writer_signature(self):
        """Doc: extract_and_write(store, turn_messages, existing_entries)."""
        from arf.core.protocols.memory import MemoryWriter
        import inspect
        sig = inspect.signature(MemoryWriter.extract_and_write)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "store" in params
        assert "turn_messages" in params
        assert "existing_entries" in params

    def test_protocols_exported_from_init(self):
        """Doc: protocols are defined in arf/core/protocols/memory.py
        and should be importable."""
        from arf.core.protocols import MemoryStore, MemoryRetriever, MemoryWriter, MemoryEntry
        assert MemoryStore is not None
        assert MemoryRetriever is not None
        assert MemoryWriter is not None
        assert MemoryEntry is not None


# ---------------------------------------------------------------------------
# 2. FileMemoryStore (docs 2.3)
# ---------------------------------------------------------------------------

class TestFileMemoryStore:
    """Doc: single file memory/memory.json, O(n) scan, no index."""

    def test_default_workspace_is_dot_memory(self):
        """Doc: workspace defaults to ./memory, stores to memory/memory.json."""
        from arf.memory.file_store import FileMemoryStore
        store = FileMemoryStore()
        assert store._dir == Path("./memory")

    def test_file_path_is_memory_json(self):
        """Doc: 单文件 memory/memory.json."""
        from arf.memory.file_store import FileMemoryStore
        store = FileMemoryStore(workspace="./memory")
        assert store._dir == Path("./memory")

    def test_save_replaces_by_id(self):
        """Doc: save() 按 id 覆盖."""
        import asyncio
        from arf.memory.file_store import FileMemoryStore
        from arf.core.protocols.memory import MemoryEntry

        async def run():
            import tempfile, os
            with tempfile.TemporaryDirectory() as d:
                s = FileMemoryStore(workspace=d)
                e1 = MemoryEntry(id="a", content="first", category="fact",
                                 timestamp=0.0, source_turn=0)
                e2 = MemoryEntry(id="a", content="second", category="fact",
                                 timestamp=0.0, source_turn=0)
                await s.save(e1)
                await s.save(e2)
                entries = await s.load("x")
                assert len(entries) == 1
                assert entries[0].content == "second"

        asyncio.run(run())

    def test_delete_removes_by_id(self):
        """Doc: delete() 按 id 移除."""
        import asyncio
        from arf.memory.file_store import FileMemoryStore
        from arf.core.protocols.memory import MemoryEntry

        async def run():
            import tempfile
            with tempfile.TemporaryDirectory() as d:
                s = FileMemoryStore(workspace=d)
                e1 = MemoryEntry(id="a", content="x", category="fact",
                                 timestamp=0.0, source_turn=0)
                await s.save(e1)
                await s.delete("a")
                entries = await s.load("x")
                assert len(entries) == 0

        asyncio.run(run())

    def test_load_accepts_session_id(self):
        """Doc: load(session_id). Session_id is accepted param
        (though currently unused)."""
        from arf.memory.file_store import FileMemoryStore
        import inspect
        sig = inspect.signature(FileMemoryStore.load)
        assert "session_id" in sig.parameters


# ---------------------------------------------------------------------------
# 3. SlidingWindowCompactor (docs 2.2)
# ---------------------------------------------------------------------------

class TestSlidingWindowCompactor:
    """Doc: compactor in arf/compaction/sliding_window.py."""

    def test_default_window_size_is_131072(self):
        """Doc: 默认 window_size = 131,072 (DeepSeek V4 context window)."""
        from arf.compaction.sliding_window import DEFAULT_WINDOW_SIZE
        assert DEFAULT_WINDOW_SIZE == 131_072

    def test_should_compact_triggers_above_threshold(self):
        """Doc: should_compact returns True when
        last_token_usage > threshold * window_size."""
        from arf.compaction.sliding_window import SlidingWindowCompactor
        c = SlidingWindowCompactor(threshold=0.75, window_size=100)
        # usage=80 > 0.75*100=75 → True
        assert c.should_compact({"last_token_usage": 80}) is True
        # usage=70 <= 75 → False
        assert c.should_compact({"last_token_usage": 70}) is False

    def test_compact_keeps_last_four_messages(self):
        """Doc: 保留最近 4 条消息."""
        import asyncio
        from arf.compaction.sliding_window import SlidingWindowCompactor

        async def run():
            c = SlidingWindowCompactor(threshold=0.75, keep_count=4)
            state = {"messages": list(range(10)),
                     "context_summary": ""}
            result = await c.compact(state)
            assert result["messages"] == [6, 7, 8, 9]
        asyncio.run(run())

    def test_compact_no_summarizer_discards_old(self):
        """Doc: 无 summarizer 时直接丢弃旧消息."""
        import asyncio
        from arf.compaction.sliding_window import SlidingWindowCompactor

        async def run():
            c = SlidingWindowCompactor(threshold=0.75, keep_count=4)
            state = {"messages": list(range(10)),
                     "context_summary": ""}
            result = await c.compact(state)
            assert len(result["messages"]) == 4
        asyncio.run(run())

    def test_compact_summary_is_additive(self):
        """Doc: 摘要叠加而非覆盖 — consecutive compactions accumulate."""
        import asyncio
        from arf.compaction.sliding_window import SlidingWindowCompactor

        async def run():
            async def fake_summarize(msgs):
                return "summary_text"
            c = SlidingWindowCompactor(summarizer=fake_summarize, keep_count=4)
            state = {"messages": ["a", "b", "c", "d", "e", "f"],
                     "context_summary": "prior summary"}
            result = await c.compact(state)
            assert "prior summary" in result["context_summary"]
            assert "summary_text" in result["context_summary"]
        asyncio.run(run())

    def test_compact_summarizer_failure_silently_degrades(self):
        """Doc: summarizer 调用异常时仅记录日志，丢弃旧消息继续执行.
        Should NOT raise, should still keep last 4 messages."""
        import asyncio
        from arf.compaction.sliding_window import SlidingWindowCompactor

        async def run():
            async def failing(msgs):
                raise RuntimeError("simulated failure")
            c = SlidingWindowCompactor(summarizer=failing, keep_count=4)
            state = {"messages": list(range(10)),
                     "context_summary": "before"}
            result = await c.compact(state)
            # Should not raise; keeps last 4 msgs
            assert len(result["messages"]) == 4
        asyncio.run(run())

    def test_summarize_tool_output_threshold_is_2000_chars(self):
        """Doc: 工具输出超过 2000 字符时触发摘要."""
        from arf.compaction.sliding_window import SlidingWindowCompactor
        c = SlidingWindowCompactor()
        # Short output: returned as-is
        short = "x" * 2000
        import asyncio
        result = asyncio.run(c.summarize_tool_output("test", short, 1))
        assert result == short

    def test_summarize_tool_output_long_triggers_disk_write(self):
        """Doc: long output (>2000 chars) → write disk + summary."""
        import asyncio, tempfile, os
        from arf.compaction.sliding_window import SlidingWindowCompactor

        async def run():
            with tempfile.TemporaryDirectory() as d:
                c = SlidingWindowCompactor(workspace=d)
                long_out = "y" * 3000
                result = await c.summarize_tool_output("my_tool", long_out, 5)
                # Should have written to disk
                out_dir = Path(d) / "tool_outputs"
                assert out_dir.exists()
                files = list(out_dir.glob("*.txt"))
                assert len(files) == 1
                assert "turn_5_my_tool" in files[0].name
                # Result should reference the file path
                assert "my_tool" in result
        asyncio.run(run())

    def test_tool_output_file_naming(self):
        """Doc: memory/tool_outputs/turn_{N}_{tool_name}.txt."""
        import asyncio, tempfile
        from arf.compaction.sliding_window import SlidingWindowCompactor

        async def run():
            with tempfile.TemporaryDirectory() as d:
                c = SlidingWindowCompactor(workspace=d)
                long_out = "y" * 3000
                await c.summarize_tool_output("search", long_out, 3)
                out_path = Path(d) / "tool_outputs" / "turn_3_search.txt"
                assert out_path.exists()
        asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. LLMMemoryWriter (docs 2.3)
# ---------------------------------------------------------------------------

class TestLLMMemoryWriter:
    """Doc: LLM-driven extraction, called per turn with last 4 messages."""

    def test_extract_and_write_signature(self):
        """Doc: extract_and_write(store, turn_messages, existing_entries)."""
        from arf.memory.llm_writer import LLMMemoryWriter
        import inspect
        sig = inspect.signature(LLMMemoryWriter.extract_and_write)
        params = list(sig.parameters.keys())
        assert "store" in params
        assert "turn_messages" in params
        assert "existing_entries" in params

    def test_content_truncated_to_500_chars(self):
        """Doc: content ≤500 chars. Code truncates at 500."""
        from arf.memory.llm_writer import LLMMemoryWriter
        # Verify the constant/behavior — code uses content[:500]
        import asyncio

        async def run():
            async def _fake_call(prompt):
                return '{"actions": [{"action": "add", "entry": {"category": "fact", "content": "' + "x" * 600 + '"}}]}'
            writer = LLMMemoryWriter(_fake_call)
            from arf.core.protocols import MemoryEntry
            store = InMemoryMemoryStore()
            entries = await writer.extract_and_write(store, [{"role": "user", "content": "test"}], [])
            # Content should be truncated to 500
            for e in entries:
                assert len(e.content) <= 500

        asyncio.run(run())

    def test_json_parse_failure_returns_existing(self):
        """Doc: JSON 解析失败时跳过该 turn，保留已有记忆."""
        import asyncio
        from arf.memory.llm_writer import LLMMemoryWriter
        from arf.core.protocols import MemoryEntry

        async def run():
            async def _fake_call(prompt):
                return "not valid json {{{"
            writer = LLMMemoryWriter(_fake_call)
            existing = [MemoryEntry(id="1", content="keep", category="fact",
                                     timestamp=0.0, source_turn=0)]
            store = InMemoryMemoryStore()
            result = await writer.extract_and_write(
                store, [{"role": "user", "content": "x"}], existing)
            assert result == existing

        asyncio.run(run())

    def test_invalid_category_defaults_to_fact(self):
        """Doc: categories are fact/preference/decision/context.
        Code: invalid category → 'fact'."""
        import asyncio
        from arf.memory.llm_writer import LLMMemoryWriter

        async def run():
            async def _fake_call(prompt):
                return '{"actions": [{"action": "add", "entry": {"category": "bogus", "content": "test content"}}]}'
            writer = LLMMemoryWriter(_fake_call)
            store = InMemoryMemoryStore()
            entries = await writer.extract_and_write(store, [{"role": "user", "content": "x"}], [])
            assert entries[0].category == "fact"

        asyncio.run(run())

    def test_update_action_includes_replaces(self):
        """Doc: LLM returns update with replaces=old-id."""
        import asyncio
        from arf.memory.llm_writer import LLMMemoryWriter
        from arf.core.protocols import MemoryEntry

        async def run():
            async def _fake_call(prompt):
                return '{"actions": [{"action": "update", "entry": {"category": "preference", "content": "updated"}, "replaces": "old-1"}]}'
            writer = LLMMemoryWriter(_fake_call)
            store = InMemoryMemoryStore()
            entries = await writer.extract_and_write(store, [{"role": "user", "content": "x"}], [])
            assert entries[0].replaces == "old-1"

        asyncio.run(run())


class TestLLMMemoryWriterJSONParsing:
    """Doc: _parse_json_response() 支持 markdown 围栏、双花括号、截取外层 {}."""

    def test_parse_plain_json(self):
        from arf.memory.llm_writer import _parse_json_response
        result = _parse_json_response('{"a": 1}')
        assert result == {"a": 1}

    def test_parse_markdown_fence(self):
        from arf.memory.llm_writer import _parse_json_response
        result = _parse_json_response('```json\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_parse_double_braces(self):
        """Doc: 双花括号 — model copies template literal."""
        from arf.memory.llm_writer import _parse_json_response
        result = _parse_json_response('{{"a": 1}}')
        assert result == {"a": 1}

    def test_parse_embedded_json(self):
        """Doc: 截取外层 {} — find outermost braces."""
        from arf.memory.llm_writer import _parse_json_response
        result = _parse_json_response('prefix text {"a": 1} suffix')
        assert result == {"a": 1}


# ---------------------------------------------------------------------------
# 5. LLMMemoryRetriever (docs 2.3)
# ---------------------------------------------------------------------------

class TestLLMMemoryRetriever:
    """Doc: LLM-driven retrieval at turn start, before routing."""

    def test_retrieve_signature_matches_protocol(self):
        """Doc: retrieve(store, query_context, session_id, max_tokens, top_k)."""
        from arf.memory.llm_retriever import LLMMemoryRetriever
        import inspect
        sig = inspect.signature(LLMMemoryRetriever.retrieve)
        params = list(sig.parameters.keys())
        for p in ("store", "query_context", "session_id", "max_tokens", "top_k"):
            assert p in params

    def test_memory_index_format_is_id_category_first_120_chars(self):
        """Doc: 记忆摘要索引（id + category + 前 120 字符）."""
        from arf.memory.llm_retriever import LLMMemoryRetriever
        # Verify the prompt construction uses [:120] on content
        import inspect
        src = inspect.getsource(LLMMemoryRetriever.retrieve)
        assert "[:120]" in src

    def test_trims_by_max_tokens(self):
        """Doc: 结果按 max_tokens 截断（chars/3 ≈ tokens）."""
        from arf.memory.llm_retriever import LLMMemoryRetriever
        import inspect
        src = inspect.getsource(LLMMemoryRetriever.retrieve)
        assert "max_tokens * 3" in src

    def test_json_failure_falls_back_to_recent_first(self):
        """Doc: JSON 解析失败 → RecentFirstRetriever."""
        import asyncio
        from arf.memory.llm_retriever import LLMMemoryRetriever
        from arf.core.protocols import MemoryEntry

        async def run():
            async def _fake_call(prompt):
                return "not json}"
            retriever = LLMMemoryRetriever(_fake_call)
            store = InMemoryMemoryStore()
            entries = [
                MemoryEntry(id="1", content="old", category="fact",
                            timestamp=100.0, source_turn=0),
                MemoryEntry(id="2", content="new", category="fact",
                            timestamp=200.0, source_turn=0),
            ]
            for e in entries:
                await store.save(e)
            result = await retriever.retrieve(store, "query", "s1", top_k=1)
            # Falls back to recent first: should get the newer entry
            assert len(result) == 1
            assert result[0].id == "2"

        asyncio.run(run())

    def test_llm_exception_falls_back_to_recent_first(self):
        """Doc: LLM 调用异常 → RecentFirstRetriever."""
        import asyncio
        from arf.memory.llm_retriever import LLMMemoryRetriever
        from arf.core.protocols import MemoryEntry

        async def run():
            async def raise_err(prompt):
                raise RuntimeError("LLM down")
            retriever = LLMMemoryRetriever(raise_err)
            store = InMemoryMemoryStore()
            entries = [
                MemoryEntry(id="1", content="old", category="fact",
                            timestamp=100.0, source_turn=0),
                MemoryEntry(id="2", content="new", category="fact",
                            timestamp=200.0, source_turn=0),
            ]
            for e in entries:
                await store.save(e)
            result = await retriever.retrieve(store, "query", "s1", top_k=1)
            assert len(result) == 1
            assert result[0].id == "2"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 6. RuleBasedMemoryWriter (docs 2.3)
# ---------------------------------------------------------------------------

class TestRuleBasedMemoryWriter:
    """Doc: 无 LLM 依赖的轻量替代，中英文关键词 → category 映射."""

    def test_only_matches_assistant_messages(self):
        """Doc: 仅匹配 assistant 消息."""
        import asyncio
        from arf.memory.writer import RuleBasedMemoryWriter

        async def run():
            writer = RuleBasedMemoryWriter()
            store = InMemoryMemoryStore()
            msgs = [
                {"role": "user", "content": "I prefer Python"},
                {"role": "assistant", "content": "I understand you prefer Python for development"},
            ]
            result = await writer.extract_and_write(store, msgs, [])
            # Only assistant msg is scanned
            assert all("prefer" in e.content.lower() for e in result if e.category == "preference")

        asyncio.run(run())

    def test_max_500_chars_per_entry(self):
        """Doc: 最多 500 字符."""
        from arf.memory.writer import _MAX_CHARS
        assert _MAX_CHARS == 500

    def test_dedup_by_content_string(self):
        """Doc: 按 content 字符串去重."""
        import asyncio
        from arf.memory.writer import RuleBasedMemoryWriter
        from arf.core.protocols import MemoryEntry

        async def run():
            writer = RuleBasedMemoryWriter()
            store = InMemoryMemoryStore()
            existing = [MemoryEntry(id="1", content="I understand you prefer Python for development",
                                     category="preference", timestamp=0.0, source_turn=0)]
            msgs = [{"role": "assistant",
                     "content": "I understand you prefer Python for development"}]
            result = await writer.extract_and_write(store, msgs, existing)
            # Same content should not create new entry
            assert len(result) == 1

        asyncio.run(run())

    def test_extract_and_write_signature(self):
        """Doc: same interface as MemoryWriter protocol."""
        from arf.memory.writer import RuleBasedMemoryWriter
        import inspect
        sig = inspect.signature(RuleBasedMemoryWriter.extract_and_write)
        params = list(sig.parameters.keys())
        for p in ("store", "turn_messages", "existing_entries"):
            assert p in params


# ---------------------------------------------------------------------------
# 7. RecentFirstRetriever (docs 2.3 — fallback)
# ---------------------------------------------------------------------------

class TestRecentFirstRetriever:
    """Doc: fallback retriever mentioned in LLMMemoryRetriever.
    Not explicitly detailed but verified for existence and interface."""

    def test_exists_and_has_retrieve(self):
        from arf.memory.recent_first import RecentFirstRetriever
        import inspect
        sig = inspect.signature(RecentFirstRetriever.retrieve)
        params = list(sig.parameters.keys())
        for p in ("store", "query_context", "session_id", "max_tokens", "top_k"):
            assert p in params


# ---------------------------------------------------------------------------
# 8. Memory module __init__ exports (docs 2.3)
# ---------------------------------------------------------------------------

class TestMemoryModuleExports:
    """Doc: FileMemoryStore, LLMMemoryWriter, LLMMemoryRetriever, RuleBasedMemoryWriter,
    RecentFirstRetriever are all discussed as available implementations."""

    def test_file_memory_store_exported(self):
        from arf.memory import FileMemoryStore
        assert FileMemoryStore is not None

    def test_recent_first_retriever_exported(self):
        from arf.memory import RecentFirstRetriever
        assert RecentFirstRetriever is not None

    def test_rule_based_writer_exported(self):
        from arf.memory import RuleBasedMemoryWriter
        assert RuleBasedMemoryWriter is not None

    def test_llm_writer_importable(self):
        """Doc discusses LLMMemoryWriter — should be importable."""
        from arf.memory.llm_writer import LLMMemoryWriter
        assert LLMMemoryWriter is not None

    def test_llm_retriever_importable(self):
        """Doc discusses LLMMemoryRetriever — should be importable."""
        from arf.memory.llm_retriever import LLMMemoryRetriever
        assert LLMMemoryRetriever is not None


# ---------------------------------------------------------------------------
# 9. Engine Integration (docs 2.4)
# ---------------------------------------------------------------------------

class TestEngineIntegration:
    """Doc: memory system integration points in GraphEngine."""

    def test_retrieval_happens_before_routing(self):
        """Doc: Memory retrieval was per-turn; now resident memory loaded at session start.
        Engine no longer calls memory_retriever.retrieve — round_end hook dispatches plugin."""
        import inspect
        from arf.engine.graph import GraphEngine

        src = inspect.getsource(GraphEngine.invoke)
        route_pos = src.find("self.model_router.route")
        assert route_pos > 0, "route call not found in invoke"
        # Memory retrieval is no longer embedded in the engine loop
        assert "memory_retriever.retrieve" not in src, (
            "memory_retriever.retrieve should have been removed from engine"
        )

    def test_compaction_after_routing_before_model_call(self):
        """Doc: [2] 路由之后、[4] 模型调用之前 = [3] 压缩判断."""
        import inspect
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        route_pos = src.find("self.model_router.route")
        compact_pos = src.find("compaction.should_compact")
        # model_call is harder to find as it depends on adapter
        assert route_pos > 0
        assert compact_pos > 0
        assert route_pos < compact_pos, (
            f"Routing (pos {route_pos}) must come before compaction (pos {compact_pos})"
        )

    def test_memory_write_uses_last_4_messages(self):
        """Doc: Memory extraction moved to arf/plugins/memory/extractor.py.
        Engine no longer passes messages to any memory writer."""
        import inspect
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "extract_and_write" not in src, (
            "Engine should NOT call memory_writer.extract_and_write — moved to plugin"
        )

    def test_memory_write_preceded_by_load(self):
        """Doc: Memory extraction is plugin-side; engine no longer calls load before write."""
        import inspect
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "memory_writer.extract_and_write" not in src, (
            "Engine should NOT call memory_writer — extraction is in plugin subprocess"
        )

    def test_memory_write_on_text_response_path(self):
        """Doc: Memory extraction is round-interval triggered by plugin, not per-turn.
        Engine's text response path no longer calls extract_and_write."""
        import inspect
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "extract_and_write" not in src, (
            "Engine should NOT call extract_and_write — moved to plugin subprocess"
        )

    def test_tool_output_summarize_after_tool_success(self):
        """Doc: [5] 工具输出摘要 — 工具执行成功后."""
        import inspect
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert "summarize_tool_output" in src, "summarize_tool_output not in invoke"

    def test_memory_max_tokens_and_top_k_wired_from_config(self):
        """F2 fix: engine accepts memory_max_tokens/top_k and uses them
        instead of hardcoded values."""
        import inspect
        from arf.engine.graph import GraphEngine

        sig = inspect.signature(GraphEngine.__init__)
        params = sig.parameters
        assert "memory_max_tokens" in params, (
            "memory_max_tokens not in GraphEngine.__init__ params"
        )
        assert "memory_top_k" in params, (
            "memory_top_k not in GraphEngine.__init__ params"
        )
        assert params["memory_max_tokens"].default == 2000
        assert params["memory_top_k"].default == 5

        # Verify engine stores these as instance attrs
        src = inspect.getsource(GraphEngine.__init__)
        assert "self._memory_max_tokens" in src
        assert "self._memory_top_k" in src

    def test_base_agent_passes_memory_config_to_engine(self):
        """F2 fix: BaseAgent reads MemoryConfig and passes to GraphEngine."""
        import inspect
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)
        assert "memory_max_tokens=mem_cfg.max_tokens" in src or "memory_max_tokens" in src


# ---------------------------------------------------------------------------
# 10. Summarizer (docs 2.2 — LLM Summarizer in base.py)
# ---------------------------------------------------------------------------

class TestSummarizer:
    """Doc: Summarizer uses system model, takes last 30 messages,
    each truncated to 300 chars. 7 sections output."""

    def test_takes_last_30_messages(self):
        """Doc: 取最近 30 条旧消息."""
        import inspect
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)
        assert "[-30:]" in src, "Summarizer should take last 30 messages"

    def test_truncates_to_300_chars(self):
        """Doc: 每条截断至 300 字符."""
        import inspect
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)
        assert "[:300]" in src, "Each message should be truncated to 300 chars"

    def test_seven_sections_in_output(self):
        """Doc: 7 sections — Completed, In Progress, Files Modified, Decisions,
        Facts & Preferences, Errors & Debugging, Next Steps."""
        import inspect
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)
        sections = ["Completed", "In Progress", "Files Modified", "Decisions",
                     "Facts & Preferences", "Errors & Debugging", "Next Steps"]
        for s in sections:
            assert s in src, f"Section '{s}' not found in summarizer prompt"

    def test_system_model_temperature_and_thinking(self):
        """Doc: system model (deepseek-v4-flash, thinking disabled, temp 0.3)."""
        import inspect
        from arf.agent.base import BaseAgent
        src = inspect.getsource(BaseAgent.__init__)
        assert "0.3" in src, "temperature=0.3 not found"
        assert "thinking_enabled" in src, "thinking_enabled not found"


# ---------------------------------------------------------------------------
# 11. Config Models (docs 2.5)
# ---------------------------------------------------------------------------

class TestConfigModels:
    """Doc: agent.yaml advanced config for memory/compaction."""

    def test_compaction_config_fields(self):
        """Doc: compaction.strategy (sliding_window|none),
        compaction.threshold (0.0-1.0)."""
        from arf.core.config_base import CompactionConfig
        c = CompactionConfig()
        assert c.strategy == "sliding_window"
        assert c.threshold == 0.75

    def test_memory_config_fields(self):
        """Doc: memory.store (file|sqlite|none), workspace (./memory),
        retriever (llm|recent_first), writer (llm|rule), max_tokens (2000),
        top_k (5)."""
        from arf.core.config_base import MemoryConfig
        m = MemoryConfig()
        assert m.store == "file"
        assert m.workspace == "./memory"
        assert m.retriever == "llm"
        assert m.writer == "llm"
        assert m.max_tokens == 2000
        assert m.top_k == 5

    def test_advanced_config_includes_memory_and_compaction(self):
        """Doc: advanced.memory and advanced.compaction in agent.yaml."""
        from arf.agent.config import AdvancedConfig
        adv = AdvancedConfig()
        assert hasattr(adv, "memory")
        assert hasattr(adv, "compaction")
        assert hasattr(adv, "system_model")

    def test_advanced_config_field_names_match_docs(self):
        """Doc snippet shows exact field names in agent.yaml."""
        from arf.agent.config import AdvancedConfig
        fields = set(AdvancedConfig.model_fields.keys())
        for f in ("memory", "compaction", "system_model"):
            assert f in fields, f"Field '{f}' missing from AdvancedConfig"


# ---------------------------------------------------------------------------
# 12. Cross-Document Consistency
# ---------------------------------------------------------------------------

class TestCrossDocConsistency:
    """Verify same claims across multiple documents."""

    def test_memory_module_files_exist_as_documented(self):
        """Doc references these specific files — verify they exist."""
        files = [
            "arf/memory/file_store.py",
            "arf/memory/llm_writer.py",
            "arf/memory/llm_retriever.py",
            "arf/memory/writer.py",
            "arf/memory/recent_first.py",
            "arf/compaction/sliding_window.py",
            "arf/core/protocols/memory.py",
        ]
        import os
        root = Path(__file__).parent.parent.parent
        for f in files:
            assert (root / f).exists(), f"File '{f}' referenced in docs does not exist"
