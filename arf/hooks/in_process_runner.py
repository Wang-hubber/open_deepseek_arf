"""InProcessHookRunner — executes blocking plugins sequentially in-process."""
import logging
from arf.core.plugin_context import PluginContext
from arf.core.protocols.plugin import PluginProtocol

logger = logging.getLogger("arf.hooks.in_process")


class InProcessHookRunner:
    """Fires blocking hooks sequentially. Side hooks are NOT handled here.

    Blocking hooks run one at a time in registration order. If a hook throws,
    subsequent hooks in the same fire() call are skipped and the exception
    propagates to the engine's error handler.
    """

    def __init__(self, plugins: list[PluginProtocol] | None = None) -> None:
        self._plugins: dict[str, list[PluginProtocol]] = {}
        self._runtime: dict = {}
        if plugins:
            for p in plugins:
                self.register(p)

    def register(self, plugin: PluginProtocol) -> None:
        for hook_name, mode in plugin.hooks.items():
            if mode == "blocking":
                self._plugins.setdefault(hook_name, []).append(plugin)
        logger.debug("Registered blocking plugin '%s' on hooks: %s",
                     plugin.name, [h for h, m in plugin.hooks.items() if m == "blocking"])

    def unregister(self, plugin_name: str) -> None:
        for hook_name in list(self._plugins.keys()):
            self._plugins[hook_name] = [
                p for p in self._plugins[hook_name]
                if p.name != plugin_name
            ]

    def update_runtime(self, **kwargs) -> None:
        self._runtime.update(kwargs)

    async def fire(self, event_type: str, ctx: PluginContext) -> None:
        """Fire all blocking plugins for this hook point sequentially.

        Raises on first plugin failure — subsequent plugins are skipped.
        """
        plugins = self._plugins.get(event_type, [])
        if not plugins:
            return

        for plugin in plugins:
            await plugin.on_hook(event_type, ctx)
