"""PeerTeamPlugin — hook-driven peer team lifecycle management."""
from __future__ import annotations

import logging
from pathlib import Path

from arf.communication.agent_bus import InMemoryAgentBus
from arf.core.plugin_context import PluginContext
from arf.core.protocols.communication import AgentInfo, AgentMessage
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
            "round_end": "side",
            "task_completed": "side",
            "session_end": "side",
        }

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "session_start":
            await self._on_session_start(ctx)
        elif hook_name == "pre_action":
            await self._on_pre_action(ctx)
        elif hook_name == "round_end":
            await self._on_round_end(ctx)
        elif hook_name == "task_completed":
            await self._on_task_completed(ctx)
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

        # SessionIndex stored in dedicated team_sessions/ directory
        _base_data = Path(ctx.data_dir or "./data")
        _session_index_dir = _base_data.parent / "team_sessions" if _base_data.name != "team_sessions" else _base_data
        idx = SessionIndex(str(_session_index_dir))
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
        """Inject pending peer messages: system context (once) + user message each."""
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
        messages = [m async for m in bus.receive(role.lower())]
        if not messages:
            return

        # Inject team communication system message once per session
        state = ctx.state
        if not state.get("_peer_context_injected"):
            state["_peer_context_injected"] = True
            teammates = [m.role for m in self._members]
            system_msg = (
                "[Team Communication]\n"
                "You are part of an agent team. Messages from teammates are "
                "delivered as user messages prefixed with [Peer]. To respond "
                "to a teammate, use the send_peer_message tool — your reply "
                "will be automatically forwarded when you complete your "
                "response (via task_complete or when the round ends).\n\n"
                f"Available teammates: {', '.join(teammates)}"
            )
            state.setdefault("messages", []).append({
                "role": "system",
                "content": system_msg,
            })

        # Inject one user message per peer message, build pending list
        pending = []
        for msg in messages:
            formatted = self._format_peer_message(msg)
            state.setdefault("messages", []).append({
                "role": "user",
                "content": formatted,
            })
            pending.append({
                "sender": msg.sender,
                "correlation_id": msg.correlation_id,
            })

        # Set pending list — merge with existing (unlikely but safe)
        existing = state.get("_pending_peer_reply", [])
        state["_pending_peer_reply"] = existing + pending

    @staticmethod
    def _format_peer_message(msg) -> str:
        """Format a peer message for injection as a user message."""
        sender = msg.sender
        msg_type = msg.type
        body = msg.payload.get("message", str(msg.payload))

        return (
            f"[Peer message from {sender}]\n"
            f"Type: {msg_type}\n\n"
            f"{body}"
        )

    # ---- round_end / task_completed: forward reply to pending senders ----

    async def _on_round_end(self, ctx: PluginContext) -> None:
        """Forward last assistant reply to all pending peer senders."""
        await self._forward_peer_reply(ctx)

    async def _on_task_completed(self, ctx: PluginContext) -> None:
        """Forward last assistant reply to all pending peer senders."""
        await self._forward_peer_reply(ctx)

    async def _forward_peer_reply(self, ctx: PluginContext) -> None:
        """Read last assistant message and forward to pending senders via AgentBus."""
        parsed = SessionIndex.parse_session_id(ctx.session_id)
        if parsed is None:
            return

        state = ctx.state
        pending = state.get("_pending_peer_reply")
        if not pending:
            return

        bus = _registry.agent_bus
        if bus is None:
            return

        # Find last assistant message with content
        messages = state.get("messages", [])
        last_reply = ""
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                last_reply = str(m["content"])
                break

        if not last_reply:
            # No reply ready yet — keep pending for the next round_end
            return

        # Only pop AFTER confirming we have a reply to forward
        state.pop("_pending_peer_reply", None)

        import uuid
        group_id, role = parsed

        for entry in pending:
            reply = AgentMessage(
                sender=role,
                receiver=entry["sender"],
                type="answer",
                payload={"message": last_reply[:2000]},
                priority="normal",
                correlation_id=f"peer_rpl_{uuid.uuid4().hex[:8]}",
            )
            await bus.send(reply)
            logger.info(
                "Peer '%s' reply (%d chars) forwarded to '%s'",
                role, len(last_reply), entry["sender"],
            )

    # ---- session_end: save group state ----

    async def _on_session_end(self, ctx: PluginContext) -> None:
        """Update member status on session end."""
        sid = ctx.session_id
        parsed = SessionIndex.parse_session_id(sid)
        if parsed is None:
            return
        group_id, role = parsed

        _base_data = Path(ctx.data_dir or "./data")
        _session_index_dir = _base_data.parent / "team_sessions" if _base_data.name != "team_sessions" else _base_data
        idx = SessionIndex(str(_session_index_dir))
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
