"""PluginProtocol — interface for hook-mounted plugins."""
from typing import Protocol, runtime_checkable
from arf.core.plugin_context import PluginContext


@runtime_checkable
class PluginProtocol(Protocol):
    """A plugin is a set of behaviors registered on Hook points.

    Plugins are NOT Tools. Tools are MCP-managed function resources
    that the Agent calls. Plugins are framework lifecycle behaviors
    that fire automatically at Hook injection points.
    """

    @property
    def name(self) -> str:
        """Unique plugin name, e.g. 'memory', 'compaction', 'trace'."""
        ...

    @property
    def hooks(self) -> list[str]:
        """Hook point names this plugin subscribes to.
        e.g. ['round_end'], ['pre_model_call', 'round_end']
        """
        ...

    async def on_hook(self, hook_name: str, context: PluginContext) -> None:
        """Called by HookRunner when a subscribed hook fires.

        Args:
            hook_name: the hook point name (e.g. 'round_end')
            context: read-only runtime context + hook-specific data
        """
        ...
