"""Tests for SubprocessHookRunner — side-only fire-and-forget behavior."""
import asyncio
from arf.hooks.runner import SubprocessHookRunner
from arf.core.plugin_context import PluginContext


class _TestSidePlugin:
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
            raise RuntimeError("side failure")


def test_side_fire_and_forget():
    p1 = _TestSidePlugin("p1", {"round_end": "side"})
    p2 = _TestSidePlugin("p2", {"round_end": "side"}, should_fail=True)
    runner = SubprocessHookRunner([p1, p2])
    ctx = PluginContext(session_id="test")

    async def _run():
        await runner.fire("round_end", ctx)
        # Give ensure_future tasks a chance to execute before loop teardown
        await asyncio.sleep(0.05)

    asyncio.run(_run())

    assert p1.fired
    assert p2.fired  # p2 was launched even though it fails


def test_ignores_blocking_plugins():
    p1 = _TestSidePlugin("p1", {"round_start": "blocking"})
    runner = SubprocessHookRunner([p1])
    ctx = PluginContext(session_id="test")

    async def _run():
        await runner.fire("round_start", ctx)
        await asyncio.sleep(0.05)

    asyncio.run(_run())

    assert not p1.fired  # blocking plugins not registered here
