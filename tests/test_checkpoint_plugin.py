"""Test CheckpointPlugin — thin RoundManager wrapper."""
import asyncio
from arf.core.plugin_context import PluginContext
from arf.plugins.checkpoint.plugin import CheckpointPlugin


class TestCheckpointPlugin:
    def test_saves_snapshot_on_round_end(self, tmp_path, monkeypatch):
        """round_start captures state, round_end closes the round."""
        monkeypatch.chdir(tmp_path)
        plugin = CheckpointPlugin()
        state = {
            "session_id": "test-session",
            "agent_name": "test-agent",
            "messages": [{"role": "user", "content": "hi"}],
            "current_turn": 3,
        }
        ctx = PluginContext(state=state)
        asyncio.run(plugin.on_hook("round_start", ctx))
        asyncio.run(plugin.on_hook("round_end", ctx))
        assert plugin.count() == 1

    def test_undo_restores_state(self, tmp_path, monkeypatch):
        """undo() should restore state from a previous round."""
        monkeypatch.chdir(tmp_path)
        plugin = CheckpointPlugin()
        state1 = {
            "session_id": "s1", "agent_name": "test",
            "messages": [], "current_turn": 1,
        }
        state2 = {
            "session_id": "s1", "agent_name": "test",
            "messages": [], "current_turn": 2,
        }
        asyncio.run(plugin.on_hook("round_start", PluginContext(state=state1)))
        asyncio.run(plugin.on_hook("round_end", PluginContext()))
        asyncio.run(plugin.on_hook("round_start", PluginContext(state=state2)))
        asyncio.run(plugin.on_hook("round_end", PluginContext()))

        # undo(2) pops both rounds and returns the oldest popped (round 1)
        restored = plugin.undo(2)
        assert restored is not None
        assert restored["current_turn"] == 1

    def test_count(self, tmp_path, monkeypatch):
        """count() should reflect the number of saved rounds."""
        monkeypatch.chdir(tmp_path)
        plugin = CheckpointPlugin()
        for i in range(3):
            state = {
                "session_id": "s1", "agent_name": "test",
                "messages": [], "current_turn": i,
            }
            asyncio.run(plugin.on_hook("round_start", PluginContext(state=state)))
            asyncio.run(plugin.on_hook("round_end", PluginContext()))

        assert plugin.count() == 3
