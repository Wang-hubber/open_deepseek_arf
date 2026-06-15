"""Test CompactionPlugin."""
import asyncio
import pytest
from arf.core.plugin_context import PluginContext


class TestCompactionPlugin:
    def test_plugin_name_and_hooks(self):
        from arf.plugins.compaction.plugin import CompactionPlugin
        plugin = CompactionPlugin()
        assert plugin.name == "compaction"
        assert plugin.hooks == {"round_end": "blocking", "tool_output": "blocking"}

    def test_skips_when_under_threshold(self):
        """Should not compact when token usage is below threshold."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.compaction.plugin import CompactionPlugin

        store = InMemoryStateStore()
        asyncio.run(store.put("test", {
            "messages": [{"role": "user", "content": "hi"}] * 20,
            "last_token_usage": 1000,  # well under 0.75 * 131072
        }))

        plugin = CompactionPlugin({"threshold": 0.75})
        plugin.set_state_store(store)
        ctx = PluginContext(session_id="test", interaction_round=5)

        asyncio.run(plugin.on_hook("round_end", ctx))

        state = asyncio.run(store.get("test"))
        assert "context_summary" not in state or state.get("context_summary", "") == ""

    def test_compacts_when_over_threshold(self):
        """Should compact when token usage exceeds threshold."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.compaction.plugin import CompactionPlugin

        store = InMemoryStateStore()
        messages = (
            [{"role": "user", "content": "msg"}] * 5 +
            [{"role": "assistant", "content": "reply"}] * 5
        )
        asyncio.run(store.put("test", {
            "messages": messages,
            "last_token_usage": 150,  # over 0.5 * 200 = 100
        }))

        plugin = CompactionPlugin({"threshold": 0.5, "window_size": 200, "keep_count": 2})
        plugin.set_state_store(store)
        ctx = PluginContext(session_id="test", interaction_round=5)

        asyncio.run(plugin.on_hook("round_end", ctx))

        state = asyncio.run(store.get("test"))
        assert len(state.get("messages", [])) < len(messages)

    def test_skips_when_few_messages(self):
        """Should not compact when there are fewer messages than keep_count."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.compaction.plugin import CompactionPlugin

        store = InMemoryStateStore()
        messages = [{"role": "user", "content": "hi"}]
        asyncio.run(store.put("test", {
            "messages": messages,
            "last_token_usage": 99999,  # high usage, but too few messages
        }))

        plugin = CompactionPlugin({"threshold": 0.01, "window_size": 100, "keep_count": 8})
        plugin.set_state_store(store)
        ctx = PluginContext(session_id="test", interaction_round=5)

        asyncio.run(plugin.on_hook("round_end", ctx))

        state = asyncio.run(store.get("test"))
        assert len(state.get("messages", [])) == len(messages)

    def test_cooldown_prevents_back_to_back_compaction(self):
        """Cooldown should skip compaction for 2 rounds after triggering."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.compaction.plugin import CompactionPlugin

        store = InMemoryStateStore()
        messages = [{"role": "user", "content": "x"}] * 20
        asyncio.run(store.put("test", {
            "messages": messages,
            "last_token_usage": 999,
        }))

        plugin = CompactionPlugin({"threshold": 0.01, "window_size": 100, "keep_count": 2})
        plugin.set_state_store(store)

        # First call: should compact
        asyncio.run(plugin.on_hook("round_end", PluginContext(
            session_id="test", interaction_round=5)))
        state = asyncio.run(store.get("test"))
        first_len = len(state.get("messages", []))

        # Reset with fresh messages, but cooldown active
        asyncio.run(store.put("test", {
            "messages": messages,
            "last_token_usage": 999,
        }))
        asyncio.run(plugin.on_hook("round_end", PluginContext(
            session_id="test", interaction_round=6)))
        state = asyncio.run(store.get("test"))
        # No compaction due to cooldown
        assert len(state.get("messages", [])) == len(messages)

    def test_compact_produces_boundary_marker(self):
        """Compaction should insert compact_boundary + isCompactSummary messages."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.compaction.plugin import CompactionPlugin

        store = InMemoryStateStore()
        messages = (
            [{"role": "user", "content": "old msg"}] * 10 +
            [{"role": "assistant", "content": "old reply"}] * 10
        )
        asyncio.run(store.put("test", {
            "messages": messages,
            "last_token_usage": 999,
        }))

        plugin = CompactionPlugin({"threshold": 0.01, "window_size": 100, "keep_count": 2})
        plugin.set_state_store(store)
        ctx = PluginContext(session_id="test", interaction_round=5)

        asyncio.run(plugin.on_hook("round_end", ctx))

        state = asyncio.run(store.get("test"))
        new_msgs = state.get("messages", [])

        # First message should be compact_boundary
        assert new_msgs[0]["role"] == "system"
        assert new_msgs[0].get("subtype") == "compact_boundary"
        assert "compactMetadata" in new_msgs[0]
        assert new_msgs[0]["compactMetadata"]["trigger"] == "auto"

        # Second message should be isCompactSummary
        assert new_msgs[1]["role"] == "user"
        assert new_msgs[1].get("isCompactSummary") is True

        # Messages should be fewer than original
        assert len(new_msgs) < len(messages)

    def test_compact_boundary_metadata(self):
        """compact_boundary should include metadata about the compaction."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.compaction.plugin import CompactionPlugin

        store = InMemoryStateStore()
        messages = (
            [{"role": "user", "content": "msg"}] * 8 +
            [{"role": "assistant", "content": "reply"}] * 8
        )
        asyncio.run(store.put("test", {
            "messages": messages,
            "last_token_usage": 999,
        }))

        plugin = CompactionPlugin({"threshold": 0.01, "window_size": 100, "keep_count": 3})
        plugin.set_state_store(store)
        ctx = PluginContext(session_id="test", interaction_round=5)

        asyncio.run(plugin.on_hook("round_end", ctx))

        state = asyncio.run(store.get("test"))
        boundary = state["messages"][0]
        meta = boundary["compactMetadata"]
        assert meta["trigger"] == "auto"
        assert meta["compactedCount"] > 0
