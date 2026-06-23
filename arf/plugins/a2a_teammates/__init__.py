"""A2A Teammates Plugin — peer-to-peer agent collaboration.

Each agent in a group is a first-class citizen with independent harness,
session, state, and trace. Communication via shared AgentBus. Park/resume
uses harness-native ctx.agent.wait() / resolve_wait().
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from arf.communication.agent_bus import InMemoryAgentBus
from arf.communication.jrpc import JrpcEnvelope
from arf.core.protocols.communication import AgentInfo, AgentMessage
from arf.harness.context import PluginContext
from arf.harness.plugin_base import Plugin
from arf.plugins.a2a_teammates.config import PeerTeamConfig
from arf.plugins.a2a_teammates.result_file import write_peer_result
from arf.plugins.a2a_teammates.state import PeerTeamState
from arf.session.session_index import SessionIndex

logger = logging.getLogger("arf.plugins.a2a_teammates")


# ═══════════════════════════════════════════════════════════════════════
# Static protocol — identical across all team sessions, prompt-cache friendly.
# ═══════════════════════════════════════════════════════════════════════
_TEAM_PROTOCOL = (
    "[Team Communication]\n"
    "Messages use JSON-RPC 2.0 semantics:\n"
    "- send_peer_message(to, message, type) to talk to a teammate.\n"
    "  This sends a JRPC request — type=\"task\" → method task.assign,\n"
    "  type=\"info\" → method info.message.  Wait for the response.\n"
    "- If you also have delegate_task available: send_peer_message is for\n"
    "  persistent teammates, delegate_task is for temporary one-off workers.\n"
    "  Do NOT use delegate_task on your teammates — use send_peer_message.\n"
    "- Messages from teammates arrive as user messages with\n"
    "  name=\"peer:<session_id>\".  Content is formatted:\n"
    "  [task assign] <message> — a task for you\n"
    "  [response] <summary> — a reply to your request\n"
    "  [info message] <text> — an informational notice\n"
    "- When you receive a task, complete it and call\n"
    "  task_complete(result=\"...\").  Your result is auto-forwarded\n"
    "  as a JRPC response to the sender.\n"
    "- After sending a task, wait for the reply — do not do the\n"
    "  receiver's work yourself."
)

_DEFAULT_EVENTS = [
    {"hook_name": "session_start", "event_name": "init", "mode": "blocking"},
    {"hook_name": "before_tools", "event_name": "inject_session_id", "mode": "blocking"},
    {"hook_name": "after_tools", "event_name": "park_after_send", "mode": "blocking"},
    {"hook_name": "before_round", "event_name": "inject_and_park", "mode": "blocking"},
    {"hook_name": "after_model", "event_name": "heartbeat", "mode": "side"},
    {"hook_name": "after_round", "event_name": "forward_reply", "mode": "side"},
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

        self._state = PeerTeamState()
        self._state.agent_bus = InMemoryAgentBus()
        # Set module-level slot so tools and _peer_wait_loop can access state
        import arf.plugins.a2a_teammates.state as _s
        _s._state = self._state

    # ==================================================================
    # handle — dispatch by event_name
    # ==================================================================

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "init":
            await self._on_init(ctx)
        elif event_name == "inject_session_id":
            await self._on_inject_session_id(ctx)
        elif event_name == "park_after_send":
            await self._on_park_after_send(ctx)
        elif event_name == "inject_and_park":
            await self._on_inject_and_park(ctx)
        elif event_name == "heartbeat":
            await self._on_heartbeat(ctx)
        elif event_name == "forward_reply":
            await self._on_forward_reply(ctx)

    # ==================================================================
    # init — create group index + inject team context
    # ==================================================================

    async def _on_init(self, ctx: PluginContext) -> None:
        sid = ctx.session_id
        parsed = SessionIndex.parse_session_id(sid)
        if parsed is None:
            return
        group_id, role = parsed

        self._state.data_dir = ctx.data_dir

        # Store harness ref for bg task wake-up
        harness_ref = ctx.hook_data.get("_harness_ref", {})
        parent_harness = harness_ref.get("harness")
        if parent_harness is not None:
            self._state.peer_harnesses[sid] = parent_harness

        # Store entry_point on registry for park decisions
        for m in self._members:
            if m.role == role:
                self._state.entry_points[sid] = m.entry_point
                break
        else:
            self._state.entry_points[sid] = False

        # SessionIndex at team_sessions/ alongside data_dir
        session_index_dir = Path(ctx.data_dir or "./data").parent / "team_sessions"
        idx = SessionIndex(str(session_index_dir))
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
        await self._state.agent_bus.register(AgentInfo(
            name=sid,
            description=f"Agent: {role}",
            capabilities=[],
        ))

        # Update own status to active
        await idx.update_member(group_id, role, {"status": "active"})

        # Inject team communication context once per session
        if sid not in self._state.context_injected_sessions:
            self._state.context_injected_sessions.add(sid)
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
                ctx.hook_data["_peer_just_sent"] = True

    # ==================================================================
    # park_after_send — park at after_tools right after send_peer_message
    # ==================================================================

    async def _on_park_after_send(self, ctx: PluginContext) -> None:
        if not ctx.hook_data.pop("_peer_just_sent", False):
            return
        await self._park_for_peer(ctx, "after_tools", f"peer_wait:{ctx.session_id}")

    # ==================================================================
    # inject_and_park — drain inbox + park at before_round
    # ==================================================================

    async def _on_inject_and_park(self, ctx: PluginContext) -> None:
        bus = self._state.agent_bus
        if bus is None:
            return
        sid = ctx.session_id

        # Drain any messages that arrived before this checkpoint
        messages = [m async for m in bus.receive(sid)]
        if messages:
            for msg in messages:
                formatted = self._format_peer_message(msg)
                ctx.agent.input(role="user", content=formatted, name=f"peer:{msg.sender}")
            return

        # Park if idle worker or waiting for reply
        has_pending = any(
            entry.get("sender") == sid
            for entry in self._state.pending_replies.values()
        )
        if not has_pending and self._state.entry_points.get(sid, False):
            return

        await self._park_for_peer(ctx, "before_round", f"peer_idle:{sid}")

    # ==================================================================
    # _park_for_peer / _format_peer_message / _package_peer_messages
    # ==================================================================

    async def _park_for_peer(self, ctx: PluginContext, hook_name: str, reason: str) -> None:
        """Park the agent and spawn a bg task waiting for peer messages."""
        sid = ctx.session_id
        harness = self._state.peer_harnesses.get(sid)
        if harness is None:
            return
        bus = self._state.agent_bus
        if bus is None:
            return

        wi = ctx.agent.wait(hook_name, reason)
        asyncio.create_task(_peer_wait_loop(
            harness=harness,
            wait_id=wi.wait_id,
            inbox_key=sid,
            bus=bus,
            cancel_evt=ctx.hook_data.get("_cancel_event"),
            pending_receiver_sid=self._get_pending_receiver_sid(sid),
            last_activity=self._state.last_activity,
        ))

    @staticmethod
    def _format_peer_message(msg) -> str:
        """Unwrap AgentMessage → plain text for LLM injection.

        JRPC payload: extract content + thin type tag.
        Legacy payload: format directly (migration fallback).
        The model sees only ``[label] message`` — no JSON, no method paths.
        """
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        if "jsonrpc" in payload:
            content = JrpcEnvelope.extract_content(payload)
            label = JrpcEnvelope.method_to_label(payload.get("method", ""))
            result_file = (payload.get("result") or {}).get("result_file", "")
            text = f"[{label}] {content}"
            if result_file:
                text += f"\n(full result: {result_file})"
            return text
        # Legacy plain payload — keep working during migration
        result_file = payload.get("result_file", "")
        content = payload.get("message", str(payload))
        prefix = f"[{msg.type}]"
        if result_file:
            return f"{prefix} {content}\n(result file: {result_file})"
        return f"{prefix} {content}"

    @staticmethod
    def _package_peer_messages(messages: list) -> dict:
        """Format messages and return an inject_message dict for resolve_wait."""
        sender_name = messages[0].sender
        parts = [PeerTeamPlugin._format_peer_message(m) for m in messages]
        return {
            "role": "user",
            "content": "\n\n".join(parts),
            "name": f"peer:{sender_name}",
        }

    # ==================================================================
    # heartbeat — update liveness timestamp
    # ==================================================================

    async def _on_heartbeat(self, ctx: PluginContext) -> None:
        import time
        self._state.last_activity[ctx.session_id] = time.time()

    # ==================================================================
    # forward_reply — send task_complete result back to pending sender
    # ==================================================================

    async def _on_forward_reply(self, ctx: PluginContext) -> None:
        """Read last assistant + task_complete result, write file, bus.send back."""
        sid = ctx.session_id

        # Check for pending reply expectations targeting this session_id
        my_pending = [
            (corr_id, entry) for corr_id, entry in self._state.pending_replies.items()
            if entry.get("receiver") == sid
        ]
        if not my_pending:
            return

        # Merge full result from task_complete + last assistant
        messages = ctx.agent.state.messages

        # Extract task_complete tool call result (preferred over assistant text)
        tc_result = ""
        found_tc = False
        for m in reversed(messages):
            if m.role == "tool" and isinstance(m.content, dict):
                name = m.content.get("name", "")
                if name.endswith("task_complete"):
                    found_tc = True
                    result_data = m.content.get("result", {})
                    if isinstance(result_data, dict):
                        tc_result = result_data.get("result", "") or ""
                    elif isinstance(result_data, str):
                        tc_result = result_data
                    break

        # Only skip if task_complete was NOT called this round.
        # If called with empty result, still forward and clear pending.
        if not found_tc:
            return

        full_result = tc_result

        # Collect tool calls from this round for result file
        tool_calls_summary: list[dict] = []
        for m in messages:
            if m.role == "assistant" and isinstance(m.content, dict):
                for tc in m.content.get("tool_calls", []):
                    tool_calls_summary.append({
                        "tool_name": tc.get("name", ""),
                        "params": tc.get("params", {}),
                    })

        bus = self._state.agent_bus
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

            reply = AgentMessage(
                sender=sid,
                receiver=sender_sid,
                type="response",
                payload=JrpcEnvelope.response(
                    id=corr_id,
                    result={
                        "summary": full_result[:300] if full_result else "(no output)",
                        "result_file": result_file,
                    },
                ),
                priority="normal",
                correlation_id=corr_id,
            )
            await bus.send(reply)

            # Clear the expectation
            self._state.pending_replies.pop(corr_id, None)
            logger.info(
                "Peer reply forwarded from %s to %s (corr=%s, file=%s)",
                sid, sender_sid, corr_id, result_file,
            )

    def _get_pending_receiver_sid(self, sender_sid: str) -> str | None:
        """Return the first receiver_sid for which sender_sid has a pending reply."""
        for entry in self._state.pending_replies.values():
            if entry.get("sender") == sender_sid:
                return entry.get("receiver")
        return None

    # ==================================================================
    # session_end — update group index
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
            await self._state.agent_bus.register(AgentInfo(
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
    pending_receiver_sid: str | None = None,
    last_activity: dict[str, float] | None = None,
) -> None:
    """Background task: detect peer message, wake harness.

    Only detects message arrival and resolves the wait — does NOT drain
    or inject.  Message consumption is handled by inject_peer_msgs at
    the next before_model, which the caller's _peer_loop triggers by
    re-entering run() after this resolves.

    If *pending_receiver_sid* is set, checks that receiver's heartbeat —
    idle for 600 s resolves the wait with a timeout notice.
    """
    import time as _time
    idle_timeout = 600.0
    poll_interval = 30.0

    while True:
        if cancel_evt is not None and cancel_evt.is_set():
            return

        has_msg = await bus.wait_for_message(
            inbox_key, timeout=poll_interval, cancel_event=cancel_evt,
        )
        if has_msg:
            messages = [m async for m in bus.receive(inbox_key)]
            if messages:
                await harness.resolve_wait(
                    wait_id,
                    inject_message=PeerTeamPlugin._package_peer_messages(messages),
                )
                return

        if cancel_evt is not None and cancel_evt.is_set():
            return

        if pending_receiver_sid is not None and last_activity is not None:
            last = last_activity.get(pending_receiver_sid, 0)
            if _time.time() - last > idle_timeout:
                await harness.resolve_wait(
                    wait_id,
                    inject_message={
                        "role": "user",
                        "name": "system",
                        "content": f"[timeout] No reply from {pending_receiver_sid} after {idle_timeout:.0f}s idle",
                    },
                )
                return


# Export Plugin class for harness loader
Plugin = PeerTeamPlugin
__all__ = ["PeerTeamPlugin", "PeerTeamConfig", "Plugin"]
