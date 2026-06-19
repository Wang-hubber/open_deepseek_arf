"""PeerTeamPlugin — hook-driven peer team lifecycle management."""
from __future__ import annotations

import asyncio
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
        """Create SessionIndex on first member's session_start + inject team context."""
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

        if existing is None:
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

        # Inject team communication system message once per session
        if not ctx.state.get("_peer_context_injected"):
            ctx.state["_peer_context_injected"] = True
            teammates = [m.role for m in self._members]
            system_msg = (
                "[Team Communication]\n"
                "You are part of an agent team. Messages from teammates are "
                "delivered as system messages prefixed with [Peer]. To respond "
                "to a teammate, use the send_peer_message tool — your reply "
                "will be automatically forwarded when you complete your "
                "response (via task_complete or when the round ends).\n\n"
                f"Available teammates: {', '.join(teammates)}"
            )
            ctx.state.setdefault("messages", []).append({
                "role": "system",
                "content": system_msg,
            })

    # ---- pre_action: inject pending peer messages ----

    async def _on_pre_action(self, ctx: PluginContext) -> None:
        """Inject pending peer messages during call_model step."""
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

        role_key = role.lower()
        messages = [m async for m in bus.receive(role_key)]
        if messages:
            await self._inject_peer_messages(ctx, role_key, messages)

    # ---- alive check + peer wake ----

    async def _check_peers_alive(self, group_id: str, my_role: str, data_dir: str) -> bool:
        """Check if any peer in the group is still active/idle/waiting."""
        try:
            _base = Path(data_dir)
            _idx_dir = _base.parent / "team_sessions" if _base.name != "team_sessions" else _base
            idx = SessionIndex(str(_idx_dir))
            index = await idx.load(group_id)
            if index is None:
                return False
            for m in index.get("members", []):
                if m["role"] == my_role:
                    continue
                if m.get("status") in ("active", "idle", "waiting_human"):
                    return True
            return False
        except Exception:
            return True  # Don't break on alive-check failure

    async def _try_wake_peers(self, ctx: PluginContext, group_id: str, my_role: str) -> None:
        """Attempt to resume the group — re-register peers on AgentBus."""
        try:
            await self.resume_group(f"{group_id}__{my_role}", str(ctx.data_dir))
            logger.info("session_park: group %s resumed for wake attempt", group_id)
        except Exception as e:
            logger.warning("session_park: failed to wake peers for group %s: %s", group_id, e)

    # ---- shared message injection ----

    async def _inject_peer_messages(
        self, ctx: PluginContext, role_key: str, messages: list
    ) -> None:
        """Inject drained peer messages as system messages into state."""
        state = ctx.state

        # Inject one system message per peer message, build pending list
        pending = []
        for msg in messages:
            formatted = self._format_peer_message(msg)
            state.setdefault("messages", []).append({
                "role": "system",
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
        """Format a peer message for injection as a system message."""
        sender = msg.sender
        msg_type = msg.type
        body = msg.payload.get("message", str(msg.payload))

        return (
            f"[Peer message from {sender}]\n"
            f"Type: {msg_type}\n\n"
            f"{body}"
        )

    # ---- park coordinator helpers ----

    @staticmethod
    def _get_park_coordinator(ctx: PluginContext):
        """Get ParkCoordinator from registry or hook_data."""
        pc = ctx.hook_data.get("_park_coordinator")
        if pc is not None:
            return pc
        return getattr(_registry, "park_coordinator", None)

    async def _inject_peer_messages_from_park(
        self, pc, state: dict, wait_id: str,
        role_key: str, messages: list,
    ) -> None:
        """Inject peer messages and complete park condition."""
        parts = []
        for msg in messages:
            formatted = self._format_peer_message(msg)
            parts.append(formatted)
        content = "\n\n".join(parts)

        await pc.complete(state, wait_id, {
            "content": content,
            "role": role_key,
        })

    async def _peer_wait_loop(
        self, pc, state: dict, wait_id: str, role_key: str,
        group_id: str, cancel_event, data_dir: str,
    ) -> None:
        """Background retry loop: wait for peer message with backoff + alive check."""
        import random

        bus = _registry.agent_bus
        if bus is None:
            return

        base_timeout = 30.0
        max_retries = 3
        backoff_factor = 2.0

        for attempt in range(max_retries):
            current = base_timeout * (backoff_factor ** attempt)
            jitter = current * 0.2 * (2 * random.random() - 1)
            current += jitter

            has_message = await bus.wait_for_message(
                role_key, timeout=current, cancel_event=cancel_event,
            )

            if has_message:
                messages = [m async for m in bus.receive(role_key)]
                if messages:
                    await self._inject_peer_messages_from_park(
                        pc, state, wait_id, role_key, messages,
                    )
                return

            if cancel_event is not None and cancel_event.is_set():
                return

            alive = await self._check_peers_alive(
                group_id, role_key, data_dir)
            if not alive and attempt < max_retries - 1:
                await self._try_wake_peers_from_park(
                    group_id, role_key, data_dir)

        logger.error(
            "peer_wait_loop: no reply after %d retries for %s",
            max_retries, role_key,
        )

    async def _try_wake_peers_from_park(
        self, group_id: str, role_key: str, data_dir: str,
    ) -> None:
        """Attempt to resume peers from within the background wait loop."""
        try:
            await self.resume_group(f"{group_id}__{role_key}", data_dir)
            logger.info(
                "peer_wait_loop: group %s resumed for wake attempt", group_id)
        except Exception as e:
            logger.warning(
                "peer_wait_loop: failed to wake peers for group %s: %s",
                group_id, e,
            )

    # ---- round_end / task_completed: forward reply to pending senders ----

    async def _on_round_end(self, ctx: PluginContext) -> None:
        """Forward last assistant reply to pending peer senders, then
        register park condition if waiting for more peer messages."""
        await self._forward_peer_reply(ctx)

        # If pending peer reply still exists, the reply wasn't ready yet —
        # keep it pending and don't park.
        state = ctx.state
        if state.get("_pending_peer_reply"):
            return

        pc = self._get_park_coordinator(ctx)
        if pc is None:
            return

        parsed = SessionIndex.parse_session_id(ctx.session_id)
        if parsed is None:
            return
        _group_id, role = parsed

        wait_id = await pc.register(
            state, "peer",
            metadata={"role": role.lower(), "group_id": _group_id},
        )

        # Spawn background wait loop
        asyncio.create_task(self._peer_wait_loop(
            pc, state, wait_id, role.lower(), _group_id,
            ctx.hook_data.get("_cancel_event"),
            str(ctx.data_dir),
        ))

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
