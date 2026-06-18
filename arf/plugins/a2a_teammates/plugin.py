"""PeerTeamPlugin — hook-driven peer team lifecycle management."""
from __future__ import annotations

import logging
from pathlib import Path

from arf.communication.agent_bus import InMemoryAgentBus
from arf.core.plugin_context import PluginContext
from arf.core.protocols.communication import AgentInfo
from arf.plugins.a2a_teammates.config import PeerTeamConfig
from arf.plugins.a2a_teammates.tools import _registry
from arf.session.session_index import SessionIndex

logger = logging.getLogger("arf.plugins.a2a_teammates")


class PeerTeamPlugin:
    """Hook plugin for peer team collaboration.

    Manages group creation, peer message injection, and recovery.
    Composes SessionIndex + InMemoryAgentBus + a2a_subagents.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = PeerTeamConfig(**(config or {}))
        self._config = cfg
        self._group_id = cfg.group_id
        self._members = cfg.members

        # Initialize shared AgentBus on registry
        if _registry.agent_bus is None:
            _registry.agent_bus = InMemoryAgentBus()

    @property
    def name(self) -> str:
        return "a2a_teammates"

    @property
    def hooks(self) -> dict[str, str]:
        return {
            "session_start": "side",
            "pre_action": "blocking",
            "session_end": "side",
        }

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "session_start":
            await self._on_session_start(ctx)
        elif hook_name == "pre_action":
            await self._on_pre_action(ctx)
        elif hook_name == "session_end":
            await self._on_session_end(ctx)

    # ---- session_start: create group index if first member ----

    async def _on_session_start(self, ctx: PluginContext) -> None:
        """Create SessionIndex on first member's session_start."""
        sid = ctx.session_id
        parsed = SessionIndex.parse_session_id(sid)
        if parsed is None:
            return
        group_id, role = parsed

        idx = SessionIndex(Path(ctx.data_dir or "./data"))
        existing = await idx.load(group_id)
        if existing is not None:
            return  # Group already created

        # Build member entries
        members = []
        for m in self._members:
            members.append({
                "role": m.role,
                "agent_name": m.agent_name,
                "session_id": f"{group_id}__{m.role}",
                "status": "active" if m.role == role else "idle",
            })

        await idx.create(group_id, members)

        # Register all members on the AgentBus
        for m in members:
            await _registry.agent_bus.register(AgentInfo(
                name=m["role"].lower(),  # normalize to lowercase
                description=f"Agent: {m['agent_name']}",
                capabilities=[],  # filled by App or dynamic discovery
            ))

        logger.info("Peer team group %s created with %d members", group_id, len(members))

    # ---- pre_action: inject pending peer messages ----

    async def _on_pre_action(self, ctx: PluginContext) -> None:
        """Inject pending peer messages into the agent's message list."""
        if ctx.current_step != "call_model":
            return

        bus = _registry.agent_bus
        if bus is None:
            return

        sid = ctx.session_id
        parsed = SessionIndex.parse_session_id(sid)
        if parsed is None:
            return
        _group_id, role = parsed

        # Drain inbox for this peer
        # Names are normalized to lowercase at registration, so we can
        # receive directly without a discover() scan.
        messages = [m async for m in bus.receive(role.lower())]
        if not messages:
            return

        for msg in messages:
            formatted = self._format_peer_message(msg)
            ctx.state.setdefault("messages", []).append({
                "role": "tool",
                "tool_call_id": msg.correlation_id,
                "content": formatted,
            })

    @staticmethod
    def _format_peer_message(msg) -> str:
        """Format a peer message for display and LLM consumption.

        Returns a structured string the frontend can parse as a peer bubble.
        """
        sender = msg.sender
        receiver = msg.receiver or "all"
        msg_type = msg.type
        body = msg.payload.get("message", str(msg.payload))
        priority = msg.priority

        prefix = ""
        if priority == "urgent":
            prefix = "[URGENT] "

        return (
            f"{prefix}[Peer] {sender} → {receiver} ({msg_type}):\n{body}"
        )

    # ---- session_end: save group state ----

    async def _on_session_end(self, ctx: PluginContext) -> None:
        """Update member status on session end."""
        sid = ctx.session_id
        parsed = SessionIndex.parse_session_id(sid)
        if parsed is None:
            return
        group_id, role = parsed

        idx = SessionIndex(Path(ctx.data_dir or "./data"))
        await idx.update_member(group_id, role, {"status": "ended"})

    # ---- recovery: resume entire group ----

    @staticmethod
    def find_group_id(session_id: str) -> str | None:
        """Parse group_id from a member session_id."""
        parsed = SessionIndex.parse_session_id(session_id)
        return parsed[0] if parsed else None

    async def resume_group(self, session_id: str, data_dir: str) -> dict | None:
        """Re-register group members on the AgentBus for recovery.

        Returns the group index dict. Callers should use the returned index
        to resume individual member sessions and their child tasks.
        """
        group_id = self.find_group_id(session_id)
        if group_id is None:
            return None

        idx = SessionIndex(data_dir)
        index = await idx.load(group_id)
        if index is None:
            return None

        # Re-register all members on the bus
        for m in index["members"]:
            await _registry.agent_bus.register(AgentInfo(
                name=m["role"].lower(),  # normalize to lowercase
                description=f"Agent: {m['agent_name']}",
                capabilities=[],
            ))

        logger.info("Peer team group %s resumed with %d members", group_id, len(index["members"]))
        return index
