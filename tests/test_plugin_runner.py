"""Test InProcessHookRunner with PluginProtocol implementations."""
import asyncio
import pytest
from arf.core.plugin_context import PluginContext


class FakeMemoryPlugin:
    """Plugin that records hook invocations."""
    def __init__(self, name: str = "memory"):
        self._name = name
        self.invocations: list[tuple[str, PluginContext]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def hooks(self) -> list[str]:
        return ["round_end"]

    async def on_hook(self, hook_name: str, context: PluginContext) -> None:
        self.invocations.append((hook_name, context))


class FakeRouterPlugin:
    """Plugin that subscribes to pre_model_call."""
    def __init__(self):
        self.routed_to: str | None = None

    @property
    def name(self) -> str:
        return "model_router"

    @property
    def hooks(self) -> list[str]:
        return ["pre_model_call"]

    async def on_hook(self, hook_name: str, context: PluginContext) -> None:
        self.routed_to = context.hook_data.get("model", "unknown")


class CrashingPlugin:
    """Plugin that always raises."""
    @property
    def name(self) -> str:
        return "crasher"

    @property
    def hooks(self) -> list[str]:
        return ["round_end"]

    async def on_hook(self, hook_name: str, context: PluginContext) -> None:
        raise RuntimeError("boom")


class TestInProcessHookRunner:
    def test_runner_fires_registered_plugins(self):
        """Plugins registered for a hook point should fire when that hook fires."""
        from arf.hooks.in_process_runner import InProcessHookRunner

        memory = FakeMemoryPlugin()
        router = FakeRouterPlugin()
        runner = InProcessHookRunner(plugins=[memory, router])

        asyncio.run(runner.fire("round_end", {"session_id": "test"}))

        assert len(memory.invocations) == 1
        assert memory.invocations[0][0] == "round_end"
        assert router.routed_to is None  # router not subscribed to round_end

    def test_runner_fires_multiple_plugins_same_hook(self):
        """Multiple plugins on the same hook should all fire."""
        from arf.hooks.in_process_runner import InProcessHookRunner

        p1 = FakeMemoryPlugin("p1")
        p2 = FakeMemoryPlugin("p2")
        runner = InProcessHookRunner(plugins=[p1, p2])

        asyncio.run(runner.fire("round_end", {}))

        assert len(p1.invocations) == 1
        assert len(p2.invocations) == 1

    def test_runner_passes_hook_data(self):
        """Plugin should receive hook_data in context."""
        from arf.hooks.in_process_runner import InProcessHookRunner

        router = FakeRouterPlugin()
        runner = InProcessHookRunner(plugins=[router])

        asyncio.run(runner.fire("pre_model_call", {"model": "deep", "messages_count": 42}))

        assert router.routed_to == "deep"

    def test_plugin_error_does_not_block_others(self):
        """One plugin's exception should not prevent other plugins from firing."""
        from arf.hooks.in_process_runner import InProcessHookRunner

        memory = FakeMemoryPlugin()
        runner = InProcessHookRunner(plugins=[CrashingPlugin(), memory])

        asyncio.run(runner.fire("round_end", {}))  # should not raise

        assert len(memory.invocations) == 1  # memory still fired

    def test_runner_unregister(self):
        """Unregistered plugin should not fire."""
        from arf.hooks.in_process_runner import InProcessHookRunner

        memory = FakeMemoryPlugin()
        runner = InProcessHookRunner(plugins=[memory])
        runner.unregister("memory")

        asyncio.run(runner.fire("round_end", {}))

        assert len(memory.invocations) == 0

    def test_register_via_method(self):
        """register() method should work same as constructor."""
        from arf.hooks.in_process_runner import InProcessHookRunner

        runner = InProcessHookRunner()
        memory = FakeMemoryPlugin()
        runner.register(memory)

        assert "round_end" in runner._plugins
        assert runner._plugins["round_end"][0] is memory
