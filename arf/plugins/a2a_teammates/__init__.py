"""A2A Teammates Plugin — peer-to-peer agent collaboration.

Each agent in a group is a first-class citizen with independent harness,
session, state, and trace. Communication via shared AgentBus. Park/resume
uses harness-native ctx.agent.wait() / resolve_wait().
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from pathlib import Path

from arf.communication.agent_bus import InMemoryAgentBus
from arf.core.protocols.communication import AgentInfo, AgentMessage
from arf.harness.context import PluginContext
from arf.harness.plugin_base import Plugin
from arf.plugins.a2a_teammates.config import PeerTeamConfig
from arf.plugins.a2a_teammates.result_file import write_peer_result
from arf.plugins.a2a_teammates.tools import _registry
from arf.session.session_index import SessionIndex

logger = logging.getLogger("arf.plugins.a2a_teammates")

# Static protocol — identical across all team sessions, prompt-cache friendly.
_TEAM_PROTOCOL = (
    "[Team Communication]\n"
    "Protocol:\n"
    "- send_peer_message(receiver, message, type) to talk to a teammate.\n"
    "  Use type=\"task\" to assign work, type=\"info\" to notify.\n"
    "- Messages from teammates arrive as [Peer <type> from <sender>] "
    "system messages. Read them and respond accordingly.\n"
    "- If you receive type=\"task\", you are expected to complete it "
    "and call task_complete(result=\"...\"). Your result will be "
    "auto-forwarded to the sender.\n"
    "- After sending a task, wait for the reply — do not do the "
    "receiver's work yourself."
)

_DEFAULT_EVENTS = [
    {"hook_name": "session_start", "event_name": "init", "mode": "blocking"},
    {"hook_name": "before_tools", "event_name": "inject_session_id", "mode": "blocking"},
    {"hook_name": "before_model", "event_name": "inject_peer_msgs", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "forward_reply", "mode": "side"},
    {"hook_name": "after_round", "event_name": "peer_park", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "session_end", "mode": "side"},
]


class PeerTeamPlugin(Plugin):
    """Manages peer-to-peer agent collaboration via AgentBus + harness park/resume."""

    def __init__(
        self,
        name: str = "a2a_teammates",
        events: list[dict] | None = None,
        config: dict | None = None,
    ) -> None:
        super().__init__(name=name, events=events or _DEFAULT_EVENTS, config=config or {})
        cfg = PeerTeamConfig(**(config or {}))
        self._group_id = cfg.group_id
        self._members = cfg.members

        # role → entry_point mapping; stored on registry by session_id at init

        if _registry.agent_bus is None:
            _registry.agent_bus = InMemoryAgentBus()

    # ==================================================================
    # handle — dispatch by event_name
    # ==================================================================

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "init":
            await self._on_init(ctx)
        elif event_name == "inject_session_id":
            await self._on_inject_session_id(ctx)
        elif event_name == "inject_peer_msgs":
            await self._on_inject_peer_msgs(ctx)
        elif event_name == "forward_reply":
            await self._on_forward_reply(ctx)
        elif event_name == "peer_park":
            await self._on_peer_park(ctx)
        elif event_name == "session_end":
            await self._on_session_end(ctx)

    # ==================================================================
    # init — create group index + inject team context
    # ==================================================================

    async def _on_init(self, ctx: PluginContext) -> None:
        sid = ctx.session_id
        parsed = SessionIndex.parse_session_id(sid)
        if parsed is None:
            return
        group_id, role = parsed

        _registry.data_dir = ctx.data_dir

        # Store harness ref for bg task wake-up
        harness_ref = ctx.hook_data.get("_harness_ref", {})
        parent_harness = harness_ref.get("harness")
        if parent_harness is not None:
            _registry._peer_harnesses[sid] = parent_harness

        # Store entry_point on registry for park decisions
        for m in self._members:
            if m.role == role:
                _registry._entry_points[sid] = m.entry_point
                break
        else:
            _registry._entry_points[sid] = False

        # SessionIndex stored in shared team_sessions/ directory
        _base_data = Path(ctx.data_dir or "./data")
        _session_index_dir = (
            _base_data.parent / "team_sessions"
            if _base_data.name != "team_sessions"
            else _base_data
        )
        idx = SessionIndex(str(_session_index_dir))
        existing = await idx.load(group_id)

        if existing is None:
            members = []
            for m in self._members:
                members.append({
                    "role": m.role,
                    "agent_name": m.agent_name,
                    "session_id": f"{group_id}__{m.role}",
                    "status": "active" if m.role == role else "idle",
                })
            if members:
                await idx.create(group_id, members)
                logger.info("Peer team group %s created with %d members", group_id, len(members))
            else:
                await idx.create(group_id, [{
                    "role": role,
                    "agent_name": role,
                    "session_id": sid,
                    "status": "active",
                }])

        # Always register THIS agent on the bus with session_id as the key.
        # Every agent registers itself — not just the group creator.
        await _registry.agent_bus.register(AgentInfo(
            name=sid,
            description=f"Agent: {role}",
            capabilities=[],
        ))

        # Update own status to active
        await idx.update_member(group_id, role, {"status": "active"})

        # Inject team communication context once per session
        if sid not in _registry._peer_context_injected:
            _registry._peer_context_injected.add(sid)
            # Dynamic roster with session_ids — LLM uses these as addresses
            roster_lines = []
            for m in self._members:
                member_sid = f"{group_id}__{m.role}"
                desc = f" — {m.agent_name}" if m.agent_name else ""
                marker = " (you)" if m.role == role else ""
                roster_lines.append(
                    f"  {m.role}{desc}{marker} → {member_sid}"
                )
            roster = "\n".join(roster_lines) if roster_lines else f"  {role} → {sid}"
            ctx.agent.input(role="system", content=(
                f"You are in team \"{group_id}\", role: \"{role}\".\n"
                f"Your session_id: {sid}\n"
                f"Teammates (use session_id for send_peer_message):\n{roster}"
            ))
            # Static protocol — cache-friendly constant
            ctx.agent.input(role="system", content=_TEAM_PROTOCOL)

    # ==================================================================
    # inject_session_id — inject session_id into send_peer_message calls
    # ==================================================================

    async def _on_inject_session_id(self, ctx: PluginContext) -> None:
        """Inject session_id into send_peer_message tool params before execution.

        The model calls send_peer_message(to=..., message=...) — it does
        not provide its own session_id.  We inject it here so the tool
        function always knows who the sender is.
        """
        tool_calls = ctx.hook_data.get("_pending_tool_calls", [])
        for tc in tool_calls:
            name = tc.get("name", "")
            if name.endswith("send_peer_message"):
                tc.setdefault("params", {})["session_id"] = ctx.session_id

    # ==================================================================
    # inject_peer_msgs — drain inbox + mid-cycle park
    # ==================================================================

    async def _on_inject_peer_msgs(self, ctx: PluginContext) -> None:
        """Drain AgentBus inbox before model call (session_id is the key)."""
        bus = _registry.agent_bus
        if bus is None:
            return

        sid = ctx.session_id

        # 1. Drain and inject any pending messages
        messages = [m async for m in bus.receive(sid)]
        if messages:
            await self._inject_messages(ctx, messages)
            return

        # 2. Mid-cycle park: only when actively waiting for a reply
        has_pending = any(
            entry.get("sender") == sid
            for entry in _registry._pending_replies.values()
        )
        if not has_pending:
            return

        harness = _registry._peer_harnesses.get(sid)
        if harness is None:
            return

        wi = ctx.agent.wait("before_model", f"peer_wait:{sid}")
        _registry._peer_wait_ids[sid] = wi.wait_id

        asyncio.create_task(_peer_wait_loop(
            harness=harness,
            wait_id=wi.wait_id,
            inbox_key=sid,
            bus=bus,
            cancel_evt=ctx.hook_data.get("_cancel_event"),
            data_dir=ctx.data_dir,
            group_id=self._group_id,
        ))

    async def _inject_messages(
        self, ctx: PluginContext, messages: list,
    ) -> None:
        """Inject peer messages as system messages into agent state."""
        for msg in messages:
            formatted = self._format_peer_message(msg)
            ctx.agent.input(role="system", content=formatted)

    @staticmethod
    def _format_peer_message(msg) -> str:
        sender = msg.sender
        msg_type = msg.type
        body = msg.payload.get("message", str(msg.payload))
        result_file = msg.payload.get("result_file", "")

        parts = [
            f"[Peer {msg_type} from {sender}]",
            "",
            body,
        ]
        if result_file:
            parts.append(f"\nFull result: {result_file}")
        return "\n".join(parts)

    # ==================================================================
    # forward_reply — send task_complete result back to pending sender
    # ==================================================================

    async def _on_forward_reply(self, ctx: PluginContext) -> None:
        """Read last assistant + task_complete result, write file, bus.send back."""
        sid = ctx.session_id

        # Check for pending reply expectations targeting this session_id
        my_pending = [
            (corr_id, entry) for corr_id, entry in _registry._pending_replies.items()
            if entry.get("receiver") == sid
        ]
        if not my_pending:
            return

        # Merge full result from task_complete + last assistant
        messages = ctx.agent.state.messages

        # Extract task_complete tool call result (preferred over assistant text)
        tc_result = ""
        for m in reversed(messages):
            if m.role == "tool" and isinstance(m.content, dict):
                name = m.content.get("name", "")
                if name.endswith("task_complete"):
                    result_data = m.content.get("result", {})
                    if isinstance(result_data, dict):
                        tc_result = result_data.get("result", "") or ""
                    elif isinstance(result_data, str):
                        tc_result = result_data
                    break

        # Fallback: last assistant message content
        last_assistant = ""
        for m in reversed(messages):
            if m.role == "assistant" and m.content:
                last_assistant = str(m.content)
                break

        # Collect tool calls from this round for result file
        tool_calls_summary: list[dict] = []
        for m in messages:
            if m.role == "assistant" and isinstance(m.content, dict):
                for tc in m.content.get("tool_calls", []):
                    tool_calls_summary.append({
                        "tool_name": tc.get("name", ""),
                        "params": tc.get("params", {}),
                    })

        # task_complete.result preferred over last assistant
        full_result = tc_result or last_assistant

        bus = _registry.agent_bus
        if bus is None:
            return

        group_id = self._group_id

        for corr_id, entry in my_pending:
            sender_sid = entry["sender"]

            # Write full result file
            result_file = write_peer_result(
                data_dir=ctx.data_dir,
                group_id=group_id,
                correlation_id=corr_id,
                agent_role=sid,
                task_description=f"Task from {sender_sid}",
                full_result=full_result,
                tool_calls=tool_calls_summary,
                turn_count=getattr(ctx, "turn", 0),
            )

            # Send brief + file pointer back
            brief = full_result[:300] if full_result else "(no output)"
            if len(full_result) > 300:
                brief += "..."

            reply = AgentMessage(
                sender=sid,
                receiver=sender_sid,
                type="reply",
                payload={
                    "brief": brief,
                    "result_file": result_file,
                    "correlation_id": corr_id,
                },
                priority="normal",
                correlation_id=f"peer_rpl_{uuid.uuid4().hex[:8]}",
            )
            await bus.send(reply)

            # Clear the expectation
            _registry._pending_replies.pop(corr_id, None)
            logger.info(
                "Peer reply forwarded from %s to %s (corr=%s, file=%s)",
                sid, sender_sid, corr_id, result_file,
            )

    # ==================================================================
    # peer_park — idle park at after_round
    # ==================================================================

    async def _on_peer_park(self, ctx: PluginContext) -> None:
        """Park at end of round if the agent is idle or waiting for a reply."""
        sid = ctx.session_id

        has_pending = any(
            entry.get("sender") == sid
            for entry in _registry._pending_replies.values()
        )
        is_entry_point = _registry._entry_points.get(sid, False)

        if not has_pending and is_entry_point:
            return  # initiator with nothing to wait for → can act

        harness = _registry._peer_harnesses.get(sid)
        if harness is None:
            return
        bus = _registry.agent_bus
        if bus is None:
            return

        wi = ctx.agent.wait("after_round", f"peer_idle:{sid}")
        _registry._peer_wait_ids[sid] = wi.wait_id

        asyncio.create_task(_peer_wait_loop(
            harness=harness,
            wait_id=wi.wait_id,
            inbox_key=sid,
            bus=bus,
            cancel_evt=ctx.hook_data.get("_cancel_event"),
            data_dir=ctx.data_dir,
            group_id=self._group_id,
        ))

    # ==================================================================
    # session_end — update group index
    # ==================================================================

    async def _on_session_end(self, ctx: PluginContext) -> None:
        sid = ctx.session_id
        parsed = SessionIndex.parse_session_id(sid)
        if parsed is None:
            return
        group_id, role = parsed

        # Clean up harness ref
        _registry._peer_harnesses.pop(sid, None)
        _registry._peer_wait_ids.pop(sid, None)
        _registry._entry_points.pop(sid, None)

        _base_data = Path(ctx.data_dir or "./data")
        _session_index_dir = (
            _base_data.parent / "team_sessions"
            if _base_data.name != "team_sessions"
            else _base_data
        )
        idx = SessionIndex(str(_session_index_dir))
        await idx.update_member(group_id, role, {"status": "ended"})

    # ==================================================================
    # group resume (public, kept from old plugin)
    # ==================================================================

    @staticmethod
    def find_group_id(session_id: str) -> str | None:
        parsed = SessionIndex.parse_session_id(session_id)
        return parsed[0] if parsed else None

    async def resume_group(self, session_id: str, data_dir: str) -> dict | None:
        group_id = self.find_group_id(session_id)
        if group_id is None:
            return None

        idx = SessionIndex(data_dir)
        index = await idx.load(group_id)
        if index is None:
            return None

        for m in index["members"]:
            await _registry.agent_bus.register(AgentInfo(
                name=m["role"].lower(),
                description=f"Agent: {m['agent_name']}",
                capabilities=[],
            ))

        logger.info("Peer team group %s resumed with %d members", group_id, len(index["members"]))
        return index


# ==================================================================
# Background: _peer_wait_loop — wait for peer reply on AgentBus
# ==================================================================

async def _peer_wait_loop(
    *,
    harness: object,
    wait_id: str,
    inbox_key: str,
    bus: object,
    cancel_evt: asyncio.Event | None,
    data_dir: str,
    group_id: str,
) -> None:
    """Background task: wait for peer message, then wake own harness.

    *inbox_key* is the agent's session_id — messages arrive on the bus
    keyed by session_id.
    """

    base_timeout = 30.0
    max_retries = 3
    backoff_factor = 2.0

    for attempt in range(max_retries):
        current = base_timeout * (backoff_factor ** attempt)
        jitter = current * 0.2 * (2 * random.random() - 1)
        current += jitter

        if bus is None:
            break

        has_msg = await bus.wait_for_message(
            inbox_key, timeout=current, cancel_event=cancel_evt,
        )

        if has_msg:
            messages = [m async for m in bus.receive(inbox_key)]
            if messages:
                parts = []
                for msg in messages:
                    formatted = PeerTeamPlugin._format_peer_message(msg)
                    parts.append(formatted)
                content = "\n\n".join(parts)
                await harness.resolve_wait(
                    wait_id,
                    inject_message={"role": "system", "content": content},
                )
                return

        if cancel_evt is not None and cancel_evt.is_set():
            return

        my_role = SessionIndex.parse_session_id(inbox_key)
        my_role = my_role[1] if my_role else inbox_key
        # Alive check — if peers dead, try resume
        if attempt < max_retries - 1:
            try:
                _base = Path(data_dir)
                _idx_dir = _base.parent / "team_sessions" if _base.name != "team_sessions" else _base
                idx = SessionIndex(str(_idx_dir))
                index = await idx.load(group_id)
                peers_alive = False
                if index:
                    for m in index.get("members", []):
                        if m["role"] == my_role:
                            continue
                        if m.get("status") in ("active", "idle", "waiting_human"):
                            peers_alive = True
                            break
                if not peers_alive:
                    # Try wake peers via resume_group
                    pass  # resume_group is on the plugin instance; skip for bg task
            except Exception:
                pass

    # Exhausted retries — resolve with timeout notice
    try:
        await harness.resolve_wait(
            wait_id,
            inject_message={
                "role": "system",
                "content": f"[Peer] No reply received for {inbox_key} after {max_retries} retries",
            },
        )
    except Exception:
        logger.exception("Failed to resolve wait for %s after timeout", inbox_key)


# Export Plugin class for harness loader
Plugin = PeerTeamPlugin
__all__ = ["PeerTeamPlugin", "PeerTeamConfig", "Plugin"]
