"""A2A Teammates Plugin — peer-to-peer agent collaboration.

Each agent in a group is a first-class citizen with independent harness,
session, state, and trace. Communication via per-agent AgentBus with a
module-level sid→bus registry so peers can discover each other's inboxes.
Park/resume uses harness-native ctx.agent.wait() / resolve_wait().
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
from arf.session.session_index import SessionIndex
from arf.plugins.a2a_teammates.state import (
    PeerTeamState,
    register_bus,
    unregister_bus,
    get_bus,
    get_pending_replies,
    get_last_activity,
    save_pending_replies,
    restore_pending_replies,
)

logger = logging.getLogger("arf.plugins.a2a_teammates")


# ═══════════════════════════════════════════════════════════════════════
# Static protocol — identical across all team sessions, prompt-cache friendly.
# ═══════════════════════════════════════════════════════════════════════
_TEAM_PROTOCOL = (
    "[Team Communication]\n"
    "Messages use JSON-RPC 2.0 semantics:\n"
    "- Use send_peer_message to assign tasks to a teammate.  Wait for\n"
    "  the reply — do not do the receiver's work yourself.\n"
    "- If you also have delegate_task available: send_peer_message is for\n"
    "  persistent teammates, delegate_task is for temporary one-off workers.\n"
    "  Do NOT use delegate_task on your teammates — use send_peer_message.\n"
    "- Messages from teammates arrive as user messages with\n"
    "  name=\"peer:<session_id>\".  Content is formatted:\n"
    "  [task assign] <message> — a task for you\n"
    "  [response] <summary> — a reply to your request\n"
    "- **CRITICAL**: When you receive a [task], you MUST call task_complete\n"
    "  when finished.  This is NOT optional — your output is BLOCKED until\n"
    "  task_complete is called.  Do not reply with plain text.\n"
    "  The framework persists your full output to disk and forwards a\n"
    "  summary to the sender — just provide both in the tool call.\n"
    "- After sending a task, wait for the reply — do not do the\n"
    "  receiver's work yourself."
)

_RETRY_TASK_COMPLETE = (
    "Your last response was BLOCKED: you have an active [task] from a "
    "teammate but did not call task_complete. "
    "You MUST complete the task and call the task_complete tool. "
    "Provide both a short summary and your full findings. "
    "Do NOT output plain text — use the tool."
)

_DEFAULT_EVENTS = [
    {"hook_name": "session_start", "event_name": "init", "mode": "blocking"},
    {"hook_name": "before_tools", "event_name": "inject_session_id", "mode": "blocking"},
    {"hook_name": "after_tools", "event_name": "park_after_send", "mode": "blocking"},
    {"hook_name": "before_round", "event_name": "inject_and_park", "mode": "blocking"},
    {"hook_name": "before_model", "event_name": "drain_inbox", "mode": "blocking"},
    {"hook_name": "after_model", "event_name": "heartbeat", "mode": "side"},
    {"hook_name": "after_round", "event_name": "forward_reply", "mode": "side"},
    {"hook_name": "before_break", "event_name": "validate_output", "mode": "blocking"},
    {"hook_name": "session_end", "event_name": "teardown", "mode": "blocking"},
]


class PeerTeamPlugin(Plugin):
    """Manages peer-to-peer agent collaboration via AgentBus + harness park/resume.

    Each plugin instance owns its own AgentBus and per-agent state.
    Cross-agent shared state (bus registry, pending_replies, last_activity)
    lives at module level in ``state.py``.
    """

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
        elif event_name == "drain_inbox":
            await self._on_drain_inbox(ctx)
        elif event_name == "heartbeat":
            await self._on_heartbeat(ctx)
        elif event_name == "forward_reply":
            await self._on_forward_reply(ctx)
        elif event_name == "validate_output":
            await self._on_validate_output(ctx)
        elif event_name == "teardown":
            await self._on_teardown(ctx)

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

        # Register this agent's bus so peers can discover it
        register_bus(sid, self._state.agent_bus)

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
            ctx.agent.input(role="system", content=_TEAM_PROTOCOL)

    # ==================================================================
    # inject_session_id — inject session_id into send_peer_message calls
    # ==================================================================

    async def _on_inject_session_id(self, ctx: PluginContext) -> None:
        """Inject session_id into send_peer_message tool params before execution."""
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

        # --- Lazy resume recovery ---
        if sid not in self._state.peer_harnesses:
            harness_ref = ctx.hook_data.get("_harness_ref", {})
            parent_harness = harness_ref.get("harness")
            if parent_harness is not None:
                self._state.peer_harnesses[sid] = parent_harness

        # Re-register on bus if not already registered
        agents = await bus.discover()
        if not any(a.name == sid for a in agents):
            parsed = SessionIndex.parse_session_id(sid)
            role = parsed[1] if parsed else sid
            await bus.register(AgentInfo(
                name=sid,
                description=f"Agent: {role}",
                capabilities=[],
            ))

        # Re-register bus in case of recovery
        if get_bus(sid) is None:
            register_bus(sid, bus)

        # Restore pending_replies from disk
        await restore_pending_replies(data_dir=ctx.data_dir)

        # Global TTL scan
        self._cleanup_stale_pending_replies()

        # Prune orphaned state entries for sessions no longer on the bus
        self._prune_stale_state(bus)

        # Drain any messages that arrived before this checkpoint
        messages = [m async for m in bus.receive(sid)]
        if messages:
            cancels, regular = self._split_cancel_messages(messages)
            for msg in regular:
                formatted = self._format_peer_message(msg)
                ctx.agent.input(role="user", content=formatted, name=f"peer:{msg.sender}")
            for corr_id, sender in cancels:
                ctx.agent.input(role="user",
                    content=f"[system] Task {corr_id} was cancelled by {sender}.",
                    name="system")
            if regular:
                return
            if not self._state.entry_points.get(sid, False):
                return

        # Park if idle worker or waiting for reply
        pending_replies = get_pending_replies()
        has_pending = any(
            entry.get("sender") == sid
            for entry in pending_replies.values()
        )
        if not has_pending and self._state.entry_points.get(sid, False):
            return

        await self._park_for_peer(ctx, "before_round", f"peer_idle:{sid}")

    # ==================================================================
    # drain_inbox — drain bus messages arriving mid-processing
    # ==================================================================

    async def _on_drain_inbox(self, ctx: PluginContext) -> None:
        """Drain any messages that arrived mid-processing."""
        bus = self._state.agent_bus
        if bus is None:
            return
        sid = ctx.session_id
        messages = [m async for m in bus.receive(sid)]
        if messages:
            cancels, regular = self._split_cancel_messages(messages)
            for msg in regular:
                formatted = self._format_peer_message(msg)
                ctx.agent.input(role="user", content=formatted, name=f"peer:{msg.sender}")
            for corr_id, sender in cancels:
                ctx.agent.input(role="user",
                    content=f"[system] Task {corr_id} was cancelled by {sender}.",
                    name="system")

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

        # Cancel any existing wait task for this session
        old_task = self._state._wait_tasks.pop(sid, None)
        if old_task is not None and not old_task.done():
            old_task.cancel()

        wi = ctx.agent.wait(hook_name, reason)

        is_idle_worker = hook_name == "before_round"
        task_idle_timeout = None if is_idle_worker else 600.0

        task = asyncio.create_task(_peer_wait_loop(
            harness=harness,
            wait_id=wi.wait_id,
            inbox_key=sid,
            bus=bus,
            cancel_evt=ctx.hook_data.get("_cancel_event"),
            pending_receiver_sid=self._get_pending_receiver_sid(sid),
            last_activity=get_last_activity(),
            idle_timeout=task_idle_timeout,
        ))
        self._state._wait_tasks[sid] = task

        def _cleanup(_fut: asyncio.Task) -> None:
            self._state._wait_tasks.pop(sid, None)

        task.add_done_callback(_cleanup)

    @staticmethod
    def _format_peer_message(msg) -> str:
        """Unwrap AgentMessage → plain text for LLM injection."""
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        if "jsonrpc" in payload:
            content = JrpcEnvelope.extract_content(payload)
            label = JrpcEnvelope.method_to_label(payload.get("method", ""))
            result_file = (payload.get("result") or {}).get("result_file", "")
            text = f"[{label}] {content}"
            if result_file:
                text += f"\n(full result: {result_file})"
            return text
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
        get_last_activity()[ctx.session_id] = time.time()

    # ==================================================================
    # forward_reply — send task_complete result back to pending sender
    # ==================================================================

    async def _on_forward_reply(self, ctx: PluginContext) -> None:
        """Read last assistant + task_complete result, write file, send back."""
        sid = ctx.session_id

        pending_replies = get_pending_replies()

        # TTL cleanup: discard stale pending entries (>30 min)
        import time as _time
        _now = _time.time()
        _pending_ttl = 1800.0
        stale = [
            corr_id for corr_id, entry in pending_replies.items()
            if entry.get("receiver") == sid
            and entry.get("created_at", 0) > 0
            and _now - entry.get("created_at", 0) > _pending_ttl
        ]
        for corr_id in stale:
            pending_replies.pop(corr_id, None)
            logger.warning("Pending reply %s expired (TTL %ss)", corr_id, _pending_ttl)

        # Check for pending reply expectations targeting this session_id
        my_pending = [
            (corr_id, entry) for corr_id, entry in pending_replies.items()
            if entry.get("receiver") == sid
        ]
        if not my_pending:
            return

        # Extract task_complete result
        messages = ctx.agent.state.messages
        tc_result = ""
        tc_summary = ""
        found_tc = False
        for m in reversed(messages):
            if m.role == "tool" and isinstance(m.content, dict):
                name = m.content.get("name", "")
                if name.endswith("task_complete"):
                    found_tc = True
                    result_data = m.content.get("result", {})
                    if isinstance(result_data, dict):
                        tc_result = result_data.get("result", "") or ""
                        tc_summary = result_data.get("summary", "") or ""
                    elif isinstance(result_data, str):
                        tc_result = result_data
                    break

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

        # Process one pending task per round
        corr_id, entry = my_pending[0]
        sender_sid = entry["sender"]

        # Always persist summary + full result to disk (audit trail)
        result_file = write_peer_result(
            data_dir=ctx.data_dir,
            group_id=self._group_id,
            correlation_id=corr_id,
            agent_role=sid,
            task_description=f"Task from {sender_sid}",
            summary=tc_summary,
            full_result=full_result,
            tool_calls=tool_calls_summary,
            turn_count=getattr(ctx, "turn", 0),
        )

        # Decide reply format: direct (short) or file reference (long)
        combined = f"{tc_summary}\n\n{full_result}".strip()
        _DIRECT_THRESHOLD = 1000
        if len(combined) <= _DIRECT_THRESHOLD:
            reply_result = {"summary": combined}
        else:
            reply_result = {
                "summary": tc_summary or (full_result[:300] if full_result else "(no output)"),
                "result_file": result_file,
            }

        reply = AgentMessage(
            sender=sid,
            receiver=sender_sid,
            type="response",
            payload=JrpcEnvelope.response(
                id=corr_id,
                result=reply_result,
            ),
            priority="normal",
            correlation_id=corr_id,
        )

        # Send reply to sender's bus so their _peer_wait_loop detects it
        sender_bus = get_bus(sender_sid)
        if sender_bus is not None:
            await sender_bus.send(reply)
        else:
            logger.warning("Cannot forward reply: sender %s not in bus registry", sender_sid)

        # Clear the expectation
        pending_replies.pop(corr_id, None)
        await save_pending_replies(data_dir=ctx.data_dir)
        logger.info(
            "Peer reply forwarded from %s to %s (corr=%s, file=%s)",
            sid, sender_sid, corr_id, result_file,
        )

        if len(my_pending) > 1:
            logger.warning(
                "Agent %s has %d pending tasks; forwarded result for %s, "
                "%d remaining for subsequent rounds",
                sid, len(my_pending), corr_id, len(my_pending) - 1,
            )

    # ==================================================================
    # validate_output — enforce task_complete for pending peer tasks
    # ==================================================================

    async def _on_validate_output(self, ctx: PluginContext) -> None:
        """Block round end if agent has pending tasks but didn't call task_complete.

        Injects a retry message and parks briefly, forcing the agent to
        take another turn with the reminder in context.
        """
        sid = ctx.session_id
        pending_replies = get_pending_replies()
        my_pending = [
            (corr_id, entry) for corr_id, entry in pending_replies.items()
            if entry.get("receiver") == sid
        ]
        if not my_pending:
            return  # no pending tasks — round can end normally

        # Check if task_complete was called
        messages = ctx.agent.state.messages
        found_tc = False
        for m in reversed(messages):
            if m.role == "tool" and isinstance(m.content, dict):
                name = m.content.get("name", "")
                if name.endswith("task_complete"):
                    found_tc = True
                    break

        if found_tc:
            return  # OK — task_complete was called

        # No task_complete despite pending task — inject reminder and retry
        ctx.agent.input(role="system", content=_RETRY_TASK_COMPLETE, name="MCP")
        logger.warning(
            "Agent %s has %d pending task(s) but did not call task_complete — retrying",
            sid, len(my_pending),
        )

        wi = ctx.agent.wait("before_break", "missing_task_complete")
        harness = self._state.peer_harnesses.get(sid)
        if harness is not None:

            async def _retry_soon() -> None:
                await asyncio.sleep(0.1)
                await harness.resolve_wait(wi.wait_id)

            asyncio.create_task(_retry_soon())

    # ==================================================================
    # teardown — clean up agent resources on session end
    # ==================================================================

    async def _on_teardown(self, ctx: PluginContext) -> None:
        """Clean up agent resources on session end."""
        sid = ctx.session_id
        bus = self._state.agent_bus
        if bus is not None:
            await bus.deregister(sid)

        # Unregister bus from shared registry
        unregister_bus(sid)

        # Cancel any running wait task
        task = self._state._wait_tasks.pop(sid, None)
        if task is not None and not task.done():
            task.cancel()

        # Clear pending replies for this agent as sender
        pending_replies = get_pending_replies()
        stale = [
            corr_id for corr_id, entry in pending_replies.items()
            if entry.get("sender") == sid
        ]
        for corr_id in stale:
            pending_replies.pop(corr_id, None)
        await save_pending_replies(data_dir=ctx.data_dir)

        # Update session_index status
        parsed = SessionIndex.parse_session_id(sid)
        if parsed is not None:
            group_id, role = parsed
            session_index_dir = Path(ctx.data_dir or "./data").parent / "team_sessions"
            idx = SessionIndex(str(session_index_dir))
            await idx.update_member(group_id, role, {"status": "inactive"})

    def _cleanup_stale_pending_replies(self) -> None:
        """Remove ALL pending_replies entries older than TTL (30 min)."""
        import time as _time
        _now = _time.time()
        _pending_ttl = 1800.0
        pending_replies = get_pending_replies()
        stale = [
            corr_id for corr_id, entry in pending_replies.items()
            if entry.get("created_at", 0) > 0
            and _now - entry.get("created_at", 0) > _pending_ttl
        ]
        for corr_id in stale:
            pending_replies.pop(corr_id, None)
            logger.warning("Global TTL: pending reply %s expired", corr_id)
        if stale:
            import asyncio as _asyncio
            _asyncio.create_task(save_pending_replies(data_dir=self._state.data_dir))

    @staticmethod
    def _split_cancel_messages(messages: list) -> tuple[list[tuple[str, str]], list]:
        """Separate cancel notifications from regular messages."""
        cancels: list[tuple[str, str]] = []
        regular: list = []
        pending_replies = get_pending_replies()
        for msg in messages:
            payload = msg.payload if isinstance(msg.payload, dict) else {}
            if payload.get("method") == JrpcEnvelope.METHOD_CANCEL:
                corr_id = payload.get("params", {}).get("correlation_id", "?")
                cancels.append((corr_id, msg.sender))
                pending_replies.pop(corr_id, None)
            else:
                regular.append(msg)
        return cancels, regular

    def _prune_stale_state(self, bus) -> None:
        """Remove state entries for sessions no longer registered on the bus."""
        registered_names = {a.name for a in bus._agents.values()} if bus else set()
        if not registered_names:
            return

        for sid in list(self._state.peer_harnesses):
            if sid not in registered_names:
                del self._state.peer_harnesses[sid]
                logger.debug("Pruned stale harness for %s", sid)

        for sid in list(self._state.context_injected_sessions):
            if sid not in registered_names:
                self._state.context_injected_sessions.discard(sid)

        for sid in list(self._state.entry_points):
            if sid not in registered_names:
                del self._state.entry_points[sid]

    def _get_pending_receiver_sid(self, sender_sid: str) -> str | None:
        """Return the first receiver_sid for which sender_sid has a pending reply."""
        for entry in get_pending_replies().values():
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
# Background: _peer_wait_loop — wait for peer reply on own AgentBus
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
    idle_timeout: float | None = None,
) -> None:
    """Background task: detect peer message on own bus, wake harness.

    If *idle_timeout* is set and *pending_receiver_sid* is provided,
    checks that receiver's heartbeat — idle for *idle_timeout* seconds
    resolves the wait with a timeout notice.

    If *idle_timeout* is None (idle worker), the loop runs indefinitely
    until a message arrives or the task is cancelled.
    """
    import time as _time
    poll_interval = 30.0

    print(f"[A2A] peer_wait_loop start | sid={inbox_key} bus={hex(id(bus))} wait_id={wait_id} idle_timeout={idle_timeout}")

    while True:
        if cancel_evt is not None and cancel_evt.is_set():
            print(f"[A2A] peer_wait_loop cancelled | sid={inbox_key}")
            return

        has_msg = await bus.wait_for_message(
            inbox_key, timeout=poll_interval, cancel_event=cancel_evt,
        )
        if has_msg:
            messages = [m async for m in bus.receive(inbox_key)]
            if messages:
                print(f"[A2A] peer_wait_loop woke | sid={inbox_key} n_msgs={len(messages)}")
                await harness.resolve_wait(
                    wait_id,
                    inject_message=PeerTeamPlugin._package_peer_messages(messages),
                )
                return

        if cancel_evt is not None and cancel_evt.is_set():
            return

        if (
            idle_timeout is not None
            and pending_receiver_sid is not None
            and last_activity is not None
        ):
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
