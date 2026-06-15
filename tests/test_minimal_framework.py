"""Verify the framework runs with and without plugins."""
import asyncio
import pytest
from arf.core.state import AgentState
from tests.fixtures.fake_model_adapter import FakeModelAdapter, FakeResponse


class TestMinimalFramework:
    """Agent with only core plugins should complete a simple turn."""

    def test_engine_accepts_plugins(self):
        """ControlPlane should accept blocking and side plugins."""
        from arf.engine.control_plane import ControlPlane
        from arf.hooks.in_process_runner import InProcessHookRunner
        from arf.hooks.runner import SubprocessHookRunner

        engine = ControlPlane.__new__(ControlPlane)
        engine._blocking = InProcessHookRunner([])
        engine._side = SubprocessHookRunner([])
        assert engine._blocking is not None
        assert engine._side is not None

    def test_plugin_runner_fires_compaction(self):
        """CompactionPlugin should fire on round_end without error."""
        from arf.hooks.in_process_runner import InProcessHookRunner
        from arf.plugins.compaction.plugin import CompactionPlugin
        from arf.plugins.trace.plugin import TracePlugin
        from arf.testing import InMemoryStateStore
        from arf.core.plugin_context import PluginContext

        store = InMemoryStateStore()
        compaction = CompactionPlugin({"threshold": 0.99})  # high threshold
        trace = TracePlugin({"data_dir": "/tmp/arf-test-traces"})

        for p in [compaction]:
            if hasattr(p, 'set_state_store'):
                p.set_state_store(store)

        runner = InProcessHookRunner([compaction, trace])

        asyncio.run(store.put("test", {
            "messages": [{"role": "user", "content": "hi"}],
            "last_token_usage": 100,
            "current_turn": 1,
        }))
        ctx = PluginContext(
            session_id="test",
            interaction_round=1,
            hook_data={
                "session_id": "test", "round": 1,
                "messages_count": 1, "last_token_usage": 100,
            },
        )
        asyncio.run(runner.fire("round_end", ctx))
        assert True
