"""Test InProcessHookRunner — blocking-only sequential fire."""
import asyncio
import pytest
from arf.hooks.in_process_runner import InProcessHookRunner
from arf.core.plugin_context import PluginContext


class _TestBlockingPlugin:
    def __init__(self, name, hooks, should_fail=False):
        self._name = name
        self._hooks = hooks
        self.should_fail = should_fail
        self.fired = False

    @property
    def name(self):
        return self._name

    @property
    def hooks(self):
        return self._hooks

    async def on_hook(self, hook_name, ctx):
        self.fired = True
        if self.should_fail:
            raise RuntimeError(f"{self._name} failed")


class TestInProcessHookRunner:
    """Blocking-only sequential fire tests."""

    def test_blocking_sequential_fire(self):
        p1 = _TestBlockingPlugin("p1", {"round_start": "blocking"})
        p2 = _TestBlockingPlugin("p2", {"round_start": "blocking"})
        runner = InProcessHookRunner([p1, p2])
        ctx = PluginContext(session_id="test")

        asyncio.run(runner.fire("round_start", ctx))

        assert p1.fired
        assert p2.fired

    def test_blocking_stops_on_first_failure(self):
        p1 = _TestBlockingPlugin("p1", {"round_start": "blocking"}, should_fail=True)
        p2 = _TestBlockingPlugin("p2", {"round_start": "blocking"})
        runner = InProcessHookRunner([p1, p2])
        ctx = PluginContext(session_id="test")

        with pytest.raises(RuntimeError, match="p1 failed"):
            asyncio.run(runner.fire("round_start", ctx))

        assert p1.fired
        assert not p2.fired

    def test_ignores_side_plugins(self):
        p1 = _TestBlockingPlugin("p1", {"round_start": "side"})
        runner = InProcessHookRunner([p1])
        ctx = PluginContext(session_id="test")

        asyncio.run(runner.fire("round_start", ctx))

        assert not p1.fired  # side plugins not registered

    def test_fire_nonexistent_hook(self):
        p1 = _TestBlockingPlugin("p1", {"round_end": "blocking"})
        runner = InProcessHookRunner([p1])
        ctx = PluginContext(session_id="test")

        asyncio.run(runner.fire("round_start", ctx))  # no plugins for this event

        assert not p1.fired  # different hook name, should not fire
