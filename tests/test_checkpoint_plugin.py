"""Test CheckpointPlugin."""
import asyncio
import json
from pathlib import Path
from arf.core.plugin_context import PluginContext


class TestCheckpointPlugin:
    def test_saves_snapshot_on_round_end(self, tmp_path):
        """round_end should save a snapshot to disk."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.checkpoint.plugin import CheckpointPlugin

        store = InMemoryStateStore()
        asyncio.run(store.put("test-session", {
            "messages": [{"role": "user", "content": "hi"}],
            "current_turn": 3,
        }))

        plugin = CheckpointPlugin({"state_dir": str(tmp_path)})
        plugin.set_state_store(store)
        ctx = PluginContext(session_id="test-session", interaction_round=3)

        asyncio.run(plugin.on_hook("round_end", ctx))

        snap_path = tmp_path / "test-session" / "snapshots" / "round_3.json"
        assert snap_path.exists()

    def test_archives_on_session_end(self, tmp_path):
        """session_end should archive final state."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.checkpoint.plugin import CheckpointPlugin

        store = InMemoryStateStore()
        asyncio.run(store.put("test-session", {
            "messages": [{"role": "user", "content": "bye"}],
            "current_turn": 10,
        }))

        plugin = CheckpointPlugin({"state_dir": str(tmp_path)})
        plugin.set_state_store(store)
        ctx = PluginContext(session_id="test-session", interaction_round=10)

        asyncio.run(plugin.on_hook("session_end", ctx))

        archive_path = tmp_path / "test-session.json"
        assert archive_path.exists()
        data = json.loads(archive_path.read_text())
        assert "tool_results" not in data

    def test_restore_snapshot(self, tmp_path):
        """Should restore state from a saved snapshot."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.checkpoint.plugin import CheckpointPlugin

        store = InMemoryStateStore()
        asyncio.run(store.put("test-session", {
            "messages": [{"role": "user", "content": "hello"}],
            "current_turn": 2,
        }))

        plugin = CheckpointPlugin({"state_dir": str(tmp_path)})
        plugin.set_state_store(store)
        ctx = PluginContext(session_id="test-session", interaction_round=2)
        asyncio.run(plugin.on_hook("round_end", ctx))

        restored = plugin.restore_snapshot("test-session", 2)
        assert restored is not None
        assert restored["current_turn"] == 2

    def test_skips_when_disabled(self, tmp_path):
        """Should not archive when disabled."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.checkpoint.plugin import CheckpointPlugin

        store = InMemoryStateStore()
        asyncio.run(store.put("test-session", {
            "messages": [],
            "current_turn": 1,
        }))

        plugin = CheckpointPlugin({"state_dir": str(tmp_path), "enabled": False})
        plugin.set_state_store(store)
        ctx = PluginContext(session_id="test-session", interaction_round=1)

        asyncio.run(plugin.on_hook("round_end", ctx))

        snap_dir = tmp_path / "test-session" / "snapshots"
        assert not snap_dir.exists()

    def test_list_snapshots(self, tmp_path):
        """Should list all saved snapshot rounds."""
        from arf.testing import InMemoryStateStore
        from arf.plugins.checkpoint.plugin import CheckpointPlugin

        store = InMemoryStateStore()
        plugin = CheckpointPlugin({"state_dir": str(tmp_path)})
        plugin.set_state_store(store)

        for turn in [1, 2, 3]:
            asyncio.run(store.put("s1", {
                "messages": [], "current_turn": turn,
            }))
            asyncio.run(plugin.on_hook("round_end", PluginContext(
                session_id="s1", interaction_round=turn)))

        turns = plugin.list_snapshots("s1")
        assert turns == [1, 2, 3]
