"""A2A Teammates Plugin — peer team collaboration."""
from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext
from arf.harness.adapter import PluginAdapter as _PluginAdapter
from arf.plugins.a2a_teammates.config import PeerTeamConfig, MemberConfig
from arf.plugins.a2a_teammates.plugin import PeerTeamPlugin as _OldPeerPlugin

_DEFAULT_EVENTS = [
    {"hook_name": "before_round", "event_name": "session_start", "mode": "side"},
    {"hook_name": "before_model", "event_name": "pre_action", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "session_park", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "round_end", "mode": "side"},
    {"hook_name": "after_round", "event_name": "task_completed", "mode": "side"},
    {"hook_name": "after_round", "event_name": "session_end", "mode": "side"},
]


class PeerTeamPlugin(_NewPlugin):
    """New-style peer team plugin wrapping existing logic."""

    def __init__(self, name="a2a_teammates", events=None, config=None):
        super().__init__(name=name, events=events or _DEFAULT_EVENTS, config=config or {})
        # Provide a minimal valid default for PeerTeamConfig
        if not self.config.get("members"):
            self.config.setdefault("members", [{"role": "default", "agent_name": "default"}])
        self._old = _OldPeerPlugin(self.config)
        self._adapter = _PluginAdapter(self._old)

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        await self._adapter.handle(event_name, ctx)


Plugin = PeerTeamPlugin
__all__ = ["PeerTeamPlugin", "PeerTeamConfig", "MemberConfig", "Plugin"]
