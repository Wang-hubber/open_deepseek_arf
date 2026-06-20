"""A2A Subagents Plugin — task delegation for agent-to-agent communication."""
from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext
from arf.harness.adapter import PluginAdapter as _PluginAdapter
from arf.plugins.a2a_subagents.config import A2APluginConfig
from arf.plugins.a2a_subagents.plugin import A2APlugin as _OldA2APlugin

_DEFAULT_EVENTS = [
    {"hook_name": "before_round", "event_name": "session_start", "mode": "side"},
    {"hook_name": "before_model", "event_name": "pre_action", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "round_end", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "session_end", "mode": "side"},
]


class A2APlugin(_NewPlugin):
    """New-style A2A subagents plugin wrapping existing logic."""

    def __init__(self, name="a2a_subagents", events=None, config=None):
        super().__init__(name=name, events=events or _DEFAULT_EVENTS, config=config or {})
        self._old = _OldA2APlugin(self.config)
        self._adapter = _PluginAdapter(self._old)

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        await self._adapter.handle(event_name, ctx)


Plugin = A2APlugin
__all__ = ["A2APlugin", "A2APluginConfig", "Plugin"]
