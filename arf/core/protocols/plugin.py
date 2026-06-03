"""PluginProtocol — interface for hook-mounted plugins."""
from typing import Literal, Protocol, runtime_checkable
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
    def hooks(self) -> dict[str, Literal["blocking", "side"]]:
        """Hook point names → execution mode.

        "blocking": engine awaits this hook; exception → error flow.
        "side": engine fires and forgets; exception swallowed silently.

        e.g. {"round_start": "blocking", "session_end": "side"}
        """
        ...

    async def on_hook(self, hook_name: str, context: PluginContext) -> None:
        """Called by HookRunner when a subscribed hook fires.

        For "blocking" hooks: exception propagates to ErrorHandler.
        For "side" hooks: exception is logged and discarded.

        Args:
            hook_name: the hook point name (e.g. 'round_end')
            context: read-only runtime context + hook-specific data
        """
        ...
