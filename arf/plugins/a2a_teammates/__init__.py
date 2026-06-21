"""A2A Teammates Plugin — peer team collaboration.

Deep port: directly extends Plugin base class.
"""
from __future__ import annotations
import logging
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.plugins.a2a_teammates.config import PeerTeamConfig, MemberConfig

logger = logging.getLogger("arf.plugins.a2a_teammates")

_DEFAULT_EVENTS = [
    {"hook_name": "before_round", "event_name": "session_start", "mode": "side"},
    {"hook_name": "before_model", "event_name": "pre_action", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "session_park", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "round_end", "mode": "side"},
    {"hook_name": "after_round", "event_name": "task_completed", "mode": "side"},
    {"hook_name": "after_round", "event_name": "session_end", "mode": "side"},
]


class PeerTeamPlugin(Plugin):
    """Manages peer-to-peer collaboration between agents."""

    def __init__(self, name="a2a_teammates", events=None, config=None):
        super().__init__(name=name, events=events or _DEFAULT_EVENTS, config=config or {})
        if not self.config.get("members"):
            self.config.setdefault("members", [{"role": "default", "agent_name": "default"}])
        self._peers: dict[str, dict] = {}

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "session_start":
            logger.debug("A2A teammates: session_start for %s", ctx.session_id)
        elif event_name == "pre_action":
            await self._route_peer_messages(ctx)
        elif event_name == "session_park":
            ctx.agent.wait(hook_name="after_round", reason="peer_park")

    async def _route_peer_messages(self, ctx: PluginContext) -> None:
        """Check for send_peer_message calls and route them."""
        pass


Plugin = PeerTeamPlugin
__all__ = ["PeerTeamPlugin", "PeerTeamConfig", "MemberConfig", "Plugin"]
