"""InProcessHookRunner — execute plugins in-process (no subprocess overhead)."""
import logging
from arf.core.plugin_context import PluginContext
from arf.core.protocols.plugin import PluginProtocol

logger = logging.getLogger("arf.hooks")


class InProcessHookRunner:
    """Runs plugins in-process. Plugins are Python objects implementing PluginProtocol.

    Unlike SubprocessHookRunner, this does not spawn subprocesses.
    Use this for framework-internal plugins (compaction, trace, checkpoint).
    External/user plugins can still use SubprocessHookRunner for isolation.
    """

    def __init__(self, plugins: list[PluginProtocol] | None = None) -> None:
        self._plugins: dict[str, list[PluginProtocol]] = {}
        self._runtime: dict = {}
        if plugins:
            for p in plugins:
                self.register(p)

    def register(self, plugin: PluginProtocol) -> None:
        """Register a plugin. Its hooks property determines which hooks it listens to."""
        for hook_name in plugin.hooks:
            self._plugins.setdefault(hook_name, []).append(plugin)
        logger.debug("Registered plugin '%s' on hooks: %s", plugin.name, plugin.hooks)

    def unregister(self, plugin_name: str) -> None:
        """Remove a plugin by name from all hook points."""
        for hook_name in list(self._plugins.keys()):
            self._plugins[hook_name] = [
                p for p in self._plugins[hook_name]
                if p.name != plugin_name
            ]

    def update_runtime(self, session_id: str | None = None,
                       interaction_round: int | None = None) -> None:
        if session_id is not None:
            self._runtime["session_id"] = session_id
        if interaction_round is not None:
            self._runtime["interaction_round"] = interaction_round

    async def fire(self, event_type: str, hook_data: dict) -> None:
        """Fire all plugins registered for this hook point."""
        plugins = self._plugins.get(event_type, [])
        if not plugins:
            return

        ctx = PluginContext(
            session_id=self._runtime.get("session_id", "default"),
            interaction_round=self._runtime.get("interaction_round", 0),
            memory_dir=self._runtime.get("memory_dir", "./memory"),
            workspace_dir=self._runtime.get("workspace_dir", "."),
            state_dir=self._runtime.get("state_dir", "./data/state"),
            trace_dir=self._runtime.get("trace_dir", "./data/traces"),
            model=self._runtime.get("system_model", "quick"),
            hook_data=hook_data,
        )

        for plugin in plugins:
            try:
                await plugin.on_hook(event_type, ctx)
            except Exception:
                logger.exception(
                    "Plugin '%s' raised on hook '%s'", plugin.name, event_type
                )
