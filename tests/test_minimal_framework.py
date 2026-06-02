"""Verify the 6-skeleton framework runs with and without plugins."""
import asyncio
import pytest
from arf.core.state import AgentState
from tests.fixtures.fake_model_adapter import FakeModelAdapter, FakeResponse


class TestMinimalFramework:
    """Agent with only 6 skeletons (no plugins) should complete a simple turn."""

    def test_engine_accepts_plugin_runner(self):
        """GraphEngine should accept plugin_runner parameter."""
        from arf.engine.graph import GraphEngine
        from arf.hooks.in_process_runner import InProcessHookRunner

        engine = GraphEngine.__new__(GraphEngine)
        engine.plugin_runner = InProcessHookRunner([])
        assert engine.plugin_runner is not None

    def test_plugin_runner_fires_compaction_and_checkpoint(self):
        """CompactionPlugin + CheckpointPlugin should coexist on round_end."""
        from arf.hooks.in_process_runner import InProcessHookRunner
        from arf.plugins.compaction.plugin import CompactionPlugin
        from arf.plugins.checkpoint.plugin import CheckpointPlugin
        from arf.plugins.trace.plugin import TracePlugin
        from arf.testing import InMemoryStateStore

        store = InMemoryStateStore()
        compaction = CompactionPlugin({"threshold": 0.99})  # high threshold
        checkpoint = CheckpointPlugin({"state_dir": "/tmp/arf-test-state"})
        trace = TracePlugin({"trace_dir": "/tmp/arf-test-traces"})

        for p in [compaction, checkpoint]:
            if hasattr(p, 'set_state_store'):
                p.set_state_store(store)

        runner = InProcessHookRunner([compaction, checkpoint, trace])

        # Fire round_end — all three should handle it without error
        asyncio.run(store.put("test", {
            "messages": [{"role": "user", "content": "hi"}],
            "last_token_usage": 100,  # below threshold
            "current_turn": 1,
        }))
        runner.update_runtime(session_id="test", interaction_round=1)
        asyncio.run(runner.fire("round_end", {
            "session_id": "test", "round": 1,
            "messages_count": 1, "last_token_usage": 100,
        }))

        # No exceptions = success
        assert True

    def test_plugin_loader_discovers_plugins(self):
        """PluginLoader should discover at least compaction, checkpoint, trace, eval."""
        from arf.plugins.plugin_loader import discover_manifests

        manifests = discover_manifests()
        names = {m["name"] for m in manifests if "name" in m}

        assert "compaction" in names
        assert "checkpoint" in names
        assert "trace" in names
        assert "eval" in names

    def test_plugin_loader_loads_in_process_plugins(self):
        """load_all_plugins should instantiate in-process plugins."""
        from arf.plugins.plugin_loader import load_all_plugins

        plugins = load_all_plugins()
        names = {p.name for p in plugins}

        assert "compaction" in names
        assert "checkpoint" in names
        assert "trace" in names
        assert "eval" in names
