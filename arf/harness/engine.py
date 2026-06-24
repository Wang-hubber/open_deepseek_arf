"""AgentHarness — execution skeleton + plugin scheduler + park/resume."""
from __future__ import annotations
import asyncio
import hashlib as _hashlib
import uuid
import logging
from collections.abc import AsyncIterator
from typing import Any
from dataclasses import asdict
from pathlib import Path

import json as _json_mod

from arf.agent.primitive import PrimitiveAgent
from arf.core.events import AgentEvent
from arf.harness.context import PluginContext
from arf.harness.plugin_base import Plugin
from arf.session.mode_manager import SessionModeManager
from arf.session.types import SessionMode

logger = logging.getLogger("arf.harness")

CHUNK_EVENTS = frozenset({"model_chunk", "thinking_delta"})

CHECKPOINTS = [
    "session_start",
    "before_round", "before_model", "after_model",
    "before_tools", "after_tools", "after_round",
    "before_break", "on_error",
    "session_end",
]


class AgentHarness:
    def __init__(
        self,
        agent: PrimitiveAgent,
        plugins: list[Plugin],
        tool_manager: Any = None,
        agent_config: Any = None,
        event_bus: Any = None,
        max_turns: int = 50,
        data_dir: str = "./data",
    ) -> None:
        self.agent = agent
        self._plugins = plugins
        self._tool_manager = tool_manager
        self._agent_config = agent_config
        self._event_bus = event_bus
        self._max_turns = max_turns
        self._data_dir = data_dir
        self._park_event: asyncio.Event | None = None
        self._parked: bool = False
        self._messages_injected: bool = False
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._interaction_round: int = 0
        self._system_prompt_text: str = ""
        self._current_ctx: PluginContext | None = None

        # HITL — harness tracks pending human-input requests
        self._hitl_waits: dict[str, str] = {}  # session_id → wait_id

        # Trace writer — async JSONL output from ctx.emit()
        self._trace_queue: asyncio.Queue = asyncio.Queue()
        self._trace_writer_task: asyncio.Task | None = None
        self._trace_file: Path | None = None

        # State persistence — harness owns session lifecycle
        from arf.engine.checkpoint import FileStateStore
        self._state_store = FileStateStore(data_dir)

        # Session mode manager
        global_mode = SessionMode.ASK
        if agent_config is not None:
            raw = getattr(agent_config, "session_mode", None)
            if raw:
                try:
                    global_mode = SessionMode(raw)
                except ValueError:
                    pass
        self._mode_manager = SessionModeManager(global_mode=global_mode)

        # Index plugins by hook_name for fast lookup
        self._by_hook: dict[str, list[Plugin]] = {c: [] for c in CHECKPOINTS}
        for p in plugins:
            for e in p.events:
                hook = e["hook_name"]
                if hook in self._by_hook:
                    self._by_hook[hook].append(p)

    # ── Tool filtering ──────────────────────────────────

    def _filter_tools(self, all_tools: list[dict]) -> list[dict]:
        """Filter tool definitions by agent config plugins/tools lists."""
        from arf.core.tool_naming import split_name

        if self._agent_config is None:
            return all_tools

        plugin_names: set[str] = set(self._agent_config.plugins) if self._agent_config.plugins else set()
        user_tool_names: set[str] | None = None
        if self._agent_config.tools:
            user_tool_names = {t.name if hasattr(t, 'name') else str(t) for t in self._agent_config.tools}

        result = []
        for t in all_tools:
            source, local_name = split_name(t["name"])
            if source == "kernel":
                result.append(t)
            elif source == "user":
                if user_tool_names is None or local_name in user_tool_names:
                    result.append(t)
            elif source in plugin_names:
                result.append(t)
            elif source not in ("kernel", "user", ""):
                # server__ or other namespace: include if unknown (future-proof)
                result.append(t)
        return result

    # ── Snapshot ──────────────────────────────────────────

    def _build_snapshot(self) -> dict:
        """Collect all agent configuration, compute hash, return {hash, config}."""
        config: dict[str, Any] = {}

        # Model -- declared config (not per-turn routing choice)
        adapter = getattr(self.agent, "_model_adapter", None)
        if adapter and hasattr(adapter, "describe"):
            config["model"] = adapter.describe()
        else:
            config["model"] = {}

        # Tools -- full definitions from resource registry
        if self._tool_manager and hasattr(self._tool_manager, "list_tools"):
            config["tools"] = self._tool_manager.list_tools()
        else:
            config["tools"] = {}

        # Skills
        if self._tool_manager and hasattr(self._tool_manager, "list_skills"):
            config["skills"] = self._tool_manager.list_skills()
        else:
            config["skills"] = {}

        # Plugins
        config["plugins"] = {
            p.name: p.config for p in self._plugins
        }

        # Memory
        memory_store = getattr(self.agent, "_memory_store", None)
        if memory_store and hasattr(memory_store, "describe"):
            config["memory"] = memory_store.describe()
        elif memory_store:
            config["memory"] = {"type": type(memory_store).__name__}
        else:
            config["memory"] = {}

        # Compaction -- from agent_config
        compaction_cfg = getattr(self._agent_config, "plugins_config", None)
        if compaction_cfg:
            config["compaction"] = compaction_cfg.get("compaction", {})
        else:
            config["compaction"] = {}

        # Routing -- from agent_config
        routing_cfg = getattr(self._agent_config, "plugins_config", None)
        if routing_cfg:
            config["routing"] = routing_cfg.get("routing", {})
        else:
            config["routing"] = {}

        # Sandbox
        config["sandbox"] = getattr(self._agent_config, "allow_paths", []) or []

        # Session mode
        config["session_mode"] = self._mode_manager.global_mode.value

        # System prompt
        config["system_prompt"] = getattr(self, "_system_prompt_text", "")

        # Canonical JSON for deterministic hash
        canonical = _json_mod.dumps(config, sort_keys=True, ensure_ascii=False)
        config_hash = _hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

        return {"hash": config_hash, "config": config}

    def _compute_diff(self, old_config: dict, new_config: dict) -> dict:
        """Return {added, removed, changed} between two snapshot configs."""
        old_keys = set(old_config.keys())
        new_keys = set(new_config.keys())
        added = sorted(new_keys - old_keys)
        removed = sorted(old_keys - new_keys)
        changed: list[str] = []
        for k in sorted(old_keys & new_keys):
            if old_config[k] != new_config[k]:
                # For dict values, show top-level key diffs
                if isinstance(old_config[k], dict) and isinstance(new_config[k], dict):
                    sub_old_keys = set(old_config[k].keys())
                    sub_new_keys = set(new_config[k].keys())
                    for sub in sorted(sub_new_keys - sub_old_keys):
                        changed.append(f"{k}.{sub}: added")
                    for sub in sorted(sub_old_keys - sub_new_keys):
                        changed.append(f"{k}.{sub}: removed")
                    for sub in sorted(sub_old_keys & sub_new_keys):
                        if old_config[k][sub] != new_config[k][sub]:
                            changed.append(f"{k}.{sub}: {old_config[k][sub]} -> {new_config[k][sub]}")
                else:
                    old_repr = _json_mod.dumps(old_config[k], ensure_ascii=False)[:100]
                    new_repr = _json_mod.dumps(new_config[k], ensure_ascii=False)[:100]
                    changed.append(f"{k}: {old_repr} -> {new_repr}")
        return {"added": added, "removed": removed, "changed": changed}

    # ── Plugin scheduling ───────────────────────────────

    def _make_ctx(self) -> PluginContext:
        return PluginContext(
            agent=self.agent,
            session_id=self.agent.state.session_id,
            event_bus=self._event_bus,
            data_dir=self._data_dir,
            trace_queue=self._trace_queue,
        )

    def _sync_ctx(self, ctx: PluginContext, turn: int) -> None:
        """Update context lifecycle counters at each checkpoint."""
        ctx.turn = turn
        ctx.interaction_round = self._interaction_round

    # ── Trace Writer ──────────────────────────────────────

    @staticmethod
    def _sanitize(obj: Any) -> Any:
        """Recursively sanitize data for JSON serialization."""
        if isinstance(obj, dict):
            return {str(k): AgentHarness._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [AgentHarness._sanitize(v) for v in obj]
        if isinstance(obj, Exception):
            return f"{type(obj).__name__}: {obj}"
        try:
            _json_mod.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)

    async def _trace_writer(self) -> None:
        """Background coroutine: drain _trace_queue, write JSONL, skip chunk events."""
        while self._trace_file is not None:
            event = await self._trace_queue.get()
            try:
                if event.type in CHUNK_EVENTS:
                    continue
                record = asdict(event)
                record["data"] = AgentHarness._sanitize(record["data"])
                try:
                    line = _json_mod.dumps(record, ensure_ascii=False) + "\n"
                except (TypeError, ValueError) as exc:
                    logger.warning("Trace serialization error: %s", exc)
                    continue
                self._trace_file.write(line)
                self._trace_file.flush()
            except OSError as exc:
                logger.warning("Trace write error: %s", exc)
            finally:
                self._trace_queue.task_done()

    def _start_trace_writer(self, session_id: str) -> None:
        """Create trace directory, open file, launch writer coroutine."""
        if self._trace_writer_task is not None and not self._trace_writer_task.done():
            return  # already running
        trace_dir = Path(self._data_dir) / session_id / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        self._trace_file = open(trace_dir / f"{session_id}.jsonl", "a", encoding="utf-8")
        self._trace_writer_task = asyncio.create_task(self._trace_writer())

    async def _stop_trace_writer(self) -> None:
        """Drain remaining events, cancel writer, close file."""
        if self._trace_writer_task is None:
            return
        await self._trace_queue.join()
        self._trace_writer_task.cancel()
        try:
            await self._trace_writer_task
        except asyncio.CancelledError:
            pass
        self._trace_writer_task = None
        if self._trace_file:
            self._trace_file.close()
            self._trace_file = None

    async def _run_blocking(self, hook_name: str, ctx: PluginContext) -> None:
        for p in self._by_hook.get(hook_name, []):
            for ename in p.event_names_for_hook(hook_name):
                if p.mode_for(hook_name, ename) == "blocking":
                    await p.handle(ename, ctx)

    def _run_side(self, hook_name: str, ctx: PluginContext) -> None:
        for p in self._by_hook.get(hook_name, []):
            for ename in p.event_names_for_hook(hook_name):
                if p.mode_for(hook_name, ename) == "side":
                    asyncio.create_task(self._safe_side(p, ename, ctx))

    async def _safe_side(self, plugin: Plugin, event_name: str, ctx: PluginContext) -> None:
        try:
            await plugin.handle(event_name, ctx)
        except Exception:
            logger.exception("Side plugin %s.%s failed", plugin.name, event_name)

    # ── Checkpoint ──────────────────────────────────────

    async def _checkpoint(self, hook_name: str, ctx: PluginContext) -> bool:
        """Run plugins at checkpoint, then check waiting. Returns True if should park."""
        ctx.hook_data["_current_hook"] = hook_name
        ctx.hook_data["_cancel_event"] = self._cancel_event
        ctx.captured_events.clear()

        # 1. Run blocking plugins
        await self._run_blocking(hook_name, ctx)

        # 2. Run side plugins (fire and forget)
        self._run_side(hook_name, ctx)

        # 3. Check waiting for this hook_name
        waiting = self.agent.state.waiting.get(hook_name, [])
        return len(waiting) > 0

    # ── Execution Loop ──────────────────────────────────

    async def run(self, user_message: str, session_id: str | None = None,
                  context_messages: list[dict] | None = None) -> AsyncIterator[AgentEvent]:
        """Main execution loop. Yields AgentEvent for SSE streaming.

        Args:
            user_message: The user's input for this round.
            session_id: Session identifier. A new session is created if this
                        differs from the current state.session_id.
            context_messages: Optional messages to inject after session setup
                              but before user_message (e.g. eval prior rounds).
        """
        agent = self.agent
        self._interaction_round += 1

        # Detect new session: empty state OR explicit session_id change
        requested_sid = session_id or ""
        is_new_session = (
            not agent.state.session_id
            or (requested_sid and requested_sid != agent.state.session_id)
        )
        if is_new_session:
            agent.state.session_id = session_id or str(uuid.uuid4())
            agent.state.messages.clear()
            agent.state.waiting.clear()

        ctx = self._make_ctx()
        self._current_ctx = ctx
        self._sync_ctx(ctx, turn=0)

        # Resolve effective session mode for this round
        effective_mode = self._mode_manager.resolve(agent_policy=None)
        ctx.hook_data["_effective_mode"] = effective_mode

        # --- session_start (new session only) ---
        if is_new_session:
            # Framework-injected system prompt (from agent.yaml)
            if self._agent_config is not None:
                from arf.agent.default_prompt_provider import DefaultSystemPromptProvider
                prompt = DefaultSystemPromptProvider(self._agent_config).build()
                self._system_prompt_text = prompt.prefix  # save for snapshot
                if prompt.prefix:
                    agent.input(role="system", content=prompt.prefix, position="begin")

            # Inject available skills inventory (MCP-style)
            if self._tool_manager is not None and hasattr(self._tool_manager, "list_skills"):
                skill_list = self._tool_manager.list_skills()
                if skill_list:
                    lines = ["## Available Skills"]
                    for s in skill_list:
                        lines.append(f"- **{s['name']}**: {s['description']}")
                    agent.input(role="system", content="\n".join(lines),
                                position=1, name="MCP")

            # Pass allow_paths to plugins at session_start (e.g. tool_guard
            # injects them as a system message so the model knows its boundaries).
            _allow_paths: list[str] = []
            if self._agent_config is not None:
                _allow_paths = getattr(self._agent_config, "allow_paths", []) or []
            ctx.hook_data["_allow_paths"] = _allow_paths

            # Pass harness config to plugins so a2a_subagents can capture
            # parent config for inline-like sub-agent creation
            ctx.hook_data["_harness_ref"] = {
                "harness": self,
                "tool_manager": self._tool_manager,
                "plugins": self._plugins,
                "agent_config": self._agent_config,
                "max_turns": self._max_turns,
            }

            # Plugins inject extra context (e.g. memory, sandbox boundaries)
            await self._checkpoint("session_start", ctx)
            for event in ctx.captured_events:
                yield event
            ctx.captured_events.clear()

            # Emit session_start lifecycle event for trace + SSE
            yield ctx.emit(event_type="session_start", data={
                "session_id": agent.state.session_id,
                "is_new": True,
                "effective_mode": effective_mode.value,
            })
        else:
            # Restore messages from persisted state (resumed session)
            existing = await self._state_store.get(agent.state.session_id)
            if existing and existing.get("messages"):
                agent.state.messages.clear()
                from arf.agent.state import Message as _M
                for m in existing["messages"]:
                    if isinstance(m, dict):
                        agent.input(role=m.get("role", "user"), content=m.get("content", ""),
                                    name=m.get("name"))
            # Set harness_ref for resumed sessions (needed by plugins like a2a_teammates)
            ctx.hook_data["_harness_ref"] = {
                "harness": self,
                "tool_manager": self._tool_manager,
                "plugins": self._plugins,
                "agent_config": self._agent_config,
                "max_turns": self._max_turns,
            }
            # Rebuild system prompt text for snapshot consistency (Finding 3)
            if self._agent_config is not None:
                from arf.agent.default_prompt_provider import DefaultSystemPromptProvider
                prompt = DefaultSystemPromptProvider(self._agent_config).build()
                self._system_prompt_text = prompt.prefix

            # Emit session_start for resumed sessions too
            yield ctx.emit(event_type="session_start", data={
                "session_id": agent.state.session_id,
                "is_new": False,
                "effective_mode": effective_mode.value,
            })

        # Start trace writer for both new AND resumed sessions (Finding 2)
        self._start_trace_writer(agent.state.session_id)

        # Build snapshot, check against persisted state
        snapshot = self._build_snapshot()
        existing = await self._state_store.get(agent.state.session_id)
        if existing and existing.get("snapshot"):
            old_hash = existing["snapshot"]["hash"]
            if old_hash != snapshot["hash"]:
                diff = self._compute_diff(existing["snapshot"]["config"], snapshot["config"])
                yield ctx.emit("config_mismatch", {
                    "old_hash": old_hash,
                    "new_hash": snapshot["hash"],
                    "diff": diff,
                })
                logger.warning("Config mismatch for session %s: %s", agent.state.session_id, diff)
        else:
            # First-time snapshot for new session
            yield ctx.emit("snapshot_created", {"hash": snapshot["hash"]})
        agent.state.snapshot = snapshot

        # Inject context messages (e.g. eval prior rounds) before user message
        if context_messages:
            for msg in context_messages:
                agent.input(role=msg["role"], content=msg["content"])

        # Inject user message
        agent.input(role="user", content=user_message)
        yield ctx.emit(event_type="user_input", data={"content": user_message})

        # --- before_round ---
        # Outer round loop: all external park/resume paths (HITL, delegate_task,
        # peer_wait) eventually loop back here, so plugins get a uniform chance
        # to inspect state (drain inbox, re-register bus, decide to park).
        while True:
            has_waiting = await self._checkpoint("before_round", ctx)

            if has_waiting and not self._messages_injected:
                yield ctx.emit(event_type="parked", data={
                    "hook_name": "before_round",
                    "waiting": agent.state.waiting,
                })
                try:
                    await self._do_park()
                except asyncio.CancelledError:
                    self._cancel_event.set()
                    if self._parked:
                        await self._save_and_teardown()
                    raise
                if self._parked:
                    # CancelledError path kept _parked=True → teardown
                    await self._save_and_teardown()
                    return
                # Normal wakeup: loop back to before_round checkpoint
                continue

            if has_waiting and self._messages_injected:
                # Messages were just injected — proceed to round so agent can process them.
                # Remaining waits will park at next round's before_round.
                self._messages_injected = False

            # No waiting / messages-injected → proceed to round

            turn = 0
            _round_restart = False

            while turn < self._max_turns:
                turn += 1
                self._sync_ctx(ctx, turn)
    
                # --- before_model ---
                if await self._checkpoint("before_model", ctx):
                    yield ctx.emit(event_type="parked", data={"hook_name": "before_model", "waiting": agent.state.waiting})
                    try:
                        await self._do_park()
                    except asyncio.CancelledError:
                        self._cancel_event.set()
                        if self._parked:
                            await self._save_and_teardown()
                        raise
                    if self._parked:
                        await self._save_and_teardown()
                        return
                    # Resume from delegate_task park → restart via before_round
                    _round_restart = True
                    break

                # Fetch tool definitions, filter, convert to OpenAI format
                openai_tools = None
                active_tool_definitions: list[dict] | None = None
                if self._tool_manager:
                    from arf.core.tool_convert import to_openai_tools
                    try:
                        all_tools = await self._tool_manager.get_tool_definitions()
                        active_tool_definitions = self._filter_tools(all_tools)
                        openai_tools = to_openai_tools(active_tool_definitions)
                    except Exception:
                        logger.exception("Failed to fetch tool definitions, proceeding without tools")
    
                # --- model_call ---
                try:
                    if agent._stream_model:
                        stream = await agent.model_call(tools=openai_tools)
                        async for chunk in stream:
                            yield ctx.emit(event_type="model_chunk", data=chunk)
                        result = stream.result
                    else:
                        result = await agent.model_call(stream=False, tools=openai_tools)
                except Exception as exc:
                    ctx.hook_data["exception"] = exc
                    await self._checkpoint("on_error", ctx)
                    yield ctx.emit(event_type="error", data={"detail": str(exc)})
                    break
    
                # Record the assistant response in agent state
                assistant_content = result.content if result.content else ""
                if result.tool_calls or result.reasoning_content:
                    msg_content: dict = {"content": assistant_content}
                    if result.tool_calls:
                        msg_content["tool_calls"] = result.tool_calls
                    if result.reasoning_content:
                        msg_content["reasoning_content"] = result.reasoning_content
                    agent.input(role="assistant", content=msg_content)
                else:
                    agent.input(role="assistant", content=assistant_content)
    
                # Emit model_call_end for downstream consumers (collect_response, tests)
                yield ctx.emit(event_type="model_call_end", data={
                    "content": result.content,
                    "reasoning_content": getattr(result, "reasoning_content", None) or None,
                    "tool_calls": result.tool_calls,
                    "usage": result.usage,
                    "finish_reason": result.finish_reason,
                    "tool_definitions": active_tool_definitions,
                })
    
                # --- after_model ---
                if await self._checkpoint("after_model", ctx):
                    yield ctx.emit(event_type="parked", data={"hook_name": "after_model", "waiting": agent.state.waiting})
                    try:
                        await self._do_park()
                    except asyncio.CancelledError:
                        self._cancel_event.set()
                        if self._parked:
                            await self._save_and_teardown()
                        raise
                    if self._parked:
                        await self._save_and_teardown()
                        return
    
                # --- tool execution ---
                if result.tool_calls and self._tool_manager:
                    # Save original — plugins may remove blocked tools from _pending_tool_calls
                    _all_tool_calls: list[dict] = list(result.tool_calls)
    
                    # --- before_tools ---
                    ctx.hook_data["_pending_tool_calls"] = result.tool_calls
    
                    # Build tool definitions lookup for permission plugins
                    # Full definition: {name, description, parameters, annotations, ...}
                    _tool_defs: dict[str, dict[str, Any]] = {}
                    if active_tool_definitions:
                        for td in active_tool_definitions:
                            _tool_defs[td["name"]] = td
                    ctx.hook_data["_tool_defs"] = _tool_defs
    
                    # Pass allow_paths from agent config for sandbox
                    _allow_paths: list[str] = []
                    if self._agent_config is not None:
                        _allow_paths = getattr(self._agent_config, "allow_paths", []) or []
                    ctx.hook_data["_allow_paths"] = _allow_paths
    
                    # Loop: re-run checkpoint after park/resume so plugins can filter
                    while True:
                        if await self._checkpoint("before_tools", ctx):
                            # Drain captured events so REPL sees approval_required etc.
                            for event in ctx.captured_events:
                                yield event
                            ctx.captured_events.clear()
                            yield ctx.emit(event_type="parked", data={"hook_name": "before_tools", "waiting": agent.state.waiting})
                            try:
                                await self._do_park()
                            except asyncio.CancelledError:
                                self._cancel_event.set()
                                if self._parked:
                                    await self._save_and_teardown()
                                raise
                            if self._parked:
                                await self._save_and_teardown()
                                return
                        else:
                            # Drain captured events from non-parking pass
                            for event in ctx.captured_events:
                                yield event
                            ctx.captured_events.clear()
                            break
    
                    # Execute tools — skip those already blocked by plugins
                    tool_calls = ctx.hook_data["_pending_tool_calls"]
                    blocked_results: dict[str, dict[str, str]] = ctx.hook_data.get("_blocked_results", {})
    
                    # Build complete call list: pending + blocked-and-removed
                    pending_ids = {tc["id"] for tc in tool_calls}
                    all_calls = list(tool_calls)
                    for tc in _all_tool_calls:
                        if tc["id"] in blocked_results and tc["id"] not in pending_ids:
                            all_calls.append(tc)
    
                    if not all_calls:
                        continue
    
                    # Emit tool_call_start for all calls (pending + blocked)
                    for tc in all_calls:
                        yield ctx.emit(event_type="tool_call_start", data={
                            "name": tc["name"], "id": tc["id"],
                            "arguments": tc.get("params", {}),
                        })
    
                    # Execute only pending (non-blocked, non-removed) calls
                    active_calls = [tc for tc in tool_calls if tc["id"] not in blocked_results]
                    if active_calls:
                        if hasattr(self._tool_manager, 'execute_batch'):
                            tool_results = await self._tool_manager.execute_batch(active_calls)
                        else:
                            tool_results = {}
                            for tc in active_calls:
                                try:
                                    tool_results[tc["id"]] = await self._tool_manager.execute(
                                        tc["name"], tc.get("params", {}))
                                except Exception as exc:
                                    tool_results[tc["id"]] = type('FakeToolResult', (), {
                                        'success': False, 'data': {}, 'error': str(exc)})()
                    else:
                        tool_results = {}
    
                    # Merge pre-existing blocked results
                    for call_id, br in blocked_results.items():
                        tool_results[call_id] = type('FakeToolResult', (), {
                            'success': False, 'data': br.get('result', ''), 'error': br.get('error', '')})()
    
                    # Inject results for ALL calls (pending + blocked)
                    for tc in all_calls:
                        r = tool_results.get(tc["id"])
                        if r is None:
                            r = type('FakeToolResult', (), {
                                'success': False, 'data': {}, 'error': 'Tool result missing'})()
    
                        agent.input(role="tool", content={
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                            "result": r.data if r.success else "",
                            "error": r.error or "",
                        })
                        yield ctx.emit(event_type="tool_call_end", data={
                            "name": tc["name"], "id": tc["id"],
                            "success": r.success,
                            "error": r.error or "",
                            "result": r.data if r.success else None,
                            "blocked": getattr(r, "blocked", False),
                        })
    
                    # --- HITL: detect pending=True in tool results ---
                    hitl_tc = None
                    hitl_data = None
                    for tc in all_calls:
                        r = tool_results.get(tc["id"])
                        if r and r.success and isinstance(r.data, dict) and r.data.get("pending"):
                            hitl_tc = tc
                            hitl_data = r.data
                            break
    
                    if hitl_tc is not None:
                        question = hitl_data.get("question", "")
                        yield ctx.emit(event_type="need_human_input", data={
                            "question": question,
                            "options": hitl_data.get("options", []),
                            "context": hitl_data.get("context", ""),
                            "task_id": hitl_data.get("task_id", ""),
                            "tool_name": hitl_tc["name"],
                            "session_id": agent.state.session_id,
                        })
                        wi = agent.wait("after_tools", "hitl")
                        self._hitl_waits[agent.state.session_id] = wi.wait_id
                        yield ctx.emit(event_type="parked", data={
                            "hook_name": "after_tools",
                            "reason": "hitl",
                            "question": question[:120],
                            "waiting": agent.state.waiting,
                        })
                        try:
                            await self._do_park()
                        except asyncio.CancelledError:
                            self._cancel_event.set()
                            if self._parked:
                                await self._save_and_teardown()
                            raise
                        if self._parked:
                            await self._save_and_teardown()
                            return
                        # Resume via before_round so plugins get a uniform
                        # chance to inspect state (inbox drain, bus recovery, etc.)
                        _round_restart = True
                        break  # exit turn loop → after_round → round_end → before_round
    
                    # --- after_tools ---
                    if await self._checkpoint("after_tools", ctx):
                        yield ctx.emit(event_type="parked", data={"hook_name": "after_tools", "waiting": agent.state.waiting})
                        try:
                            await self._do_park()
                        except asyncio.CancelledError:
                            self._cancel_event.set()
                            if self._parked:
                                await self._save_and_teardown()
                            raise
                        if self._parked:
                            await self._save_and_teardown()
                            return
    
                    continue  # loop back to before_model
    
                # No tool calls — plugins may intercept to force retry
                if await self._checkpoint("before_break", ctx):
                    for event in ctx.captured_events:
                        yield event
                    ctx.captured_events.clear()
                    yield ctx.emit(event_type="parked", data={"hook_name": "before_break", "waiting": agent.state.waiting})
                    try:
                        await self._do_park()
                    except asyncio.CancelledError:
                        self._cancel_event.set()
                        if self._parked:
                            await self._save_and_teardown()
                        raise
                    if self._parked:
                        await self._save_and_teardown()
                        return
                    continue  # retry — plugin wants another turn
    
                break  # no tool_calls → round done

            # --- after_round ---
            if await self._checkpoint("after_round", ctx):
                yield ctx.emit(event_type="parked", data={"hook_name": "after_round", "waiting": agent.state.waiting})
                try:
                    await self._do_park()
                except asyncio.CancelledError:
                    self._cancel_event.set()
                    if self._parked:
                        await self._save_and_teardown()
                    raise
                if self._parked:
                    await self._save_and_teardown()
                    return
    
            yield ctx.emit(event_type="round_end", data={
                "round": self._interaction_round,
                "turns": turn,
                "stopped": "max_turns" if turn >= self._max_turns else "completed",
            })

            if _round_restart:
                continue  # resume via before_round for next round

            await self._save_and_teardown()
            yield ctx.emit(event_type="session_end", data={
                "session_id": agent.state.session_id,
            })

    # ── State Persistence ───────────────────────────────

    async def _save_and_teardown(self) -> None:
        """Stop trace writer, save state, fire session_end — called on all exit paths."""
        await self._stop_trace_writer()
        await self._save_state()
        # Fire session_end lifecycle so plugins clean up (deregister, cancel tasks, etc.)
        if self._current_ctx is not None:
            try:
                await self._checkpoint("session_end", self._current_ctx)
            except Exception:
                logger.exception("session_end checkpoint failed")

    async def _save_state(self) -> None:
        """Persist current agent state so sessions survive restarts."""
        state = self.agent.state
        msgs = [{"role": m.role, "content": m.content, "name": m.name} for m in state.messages]
        await self._state_store.put(state.session_id, {
            "session_id": state.session_id,
            "messages": msgs,
            "snapshot": state.snapshot,
            "session_active": True,
        })

    # ── Session Mode ────────────────────────────────────

    def set_session_mode(self, mode: str | SessionMode) -> None:
        """Switch session mode at runtime (e.g. /mode command)."""
        if isinstance(mode, str):
            mode = SessionMode(mode)
        self._mode_manager.set_global(mode)
        if self._event_bus:
            self._event_bus.emit(AgentEvent(
                type="session_policy_switch",
                data={"new_mode": mode.value},
                session_id=self.agent.state.session_id,
            ))

    # ── Park / Resume ────────────────────────────────────

    async def _do_park(self) -> None:
        """Block until external resolve_wait() wakes the harness (partial or full)."""
        if not any(self.agent.state.waiting.values()):
            return
        self._park_event = asyncio.Event()
        self._parked = True
        await self._park_event.wait()

    async def resolve_wait(self, wait_id: str, inject_message: dict | None = None) -> bool:
        """External call: finish a wait + optionally inject a message.

        Always wakes the harness (sets _park_event, clears _parked).
        Returns True only when ALL waits are resolved.
        """
        if inject_message:
            self.agent.input(
                role=inject_message.get("role", "user"),
                content=inject_message.get("content", ""),
                name=inject_message.get("name"),
            )
            self._messages_injected = True

        self.agent.finish_wait(wait_id=wait_id)
        self._parked = False
        if self._park_event:
            self._park_event.set()

        return not bool(self.agent.state.waiting)

    async def provide_hitl_response(self, session_id: str, answer: str) -> bool:
        """Provide a human response to a pending HITL request.

        Called by CLI / frontend after the user answers an ask_user prompt.
        Injects the answer as a user message and resolves the harness park,
        so the agent continues its ReAct loop with the human's input.
        """
        wait_id = self._hitl_waits.pop(session_id, None)
        if wait_id is None:
            return False
        return await self.resolve_wait(wait_id, inject_message={
            "role": "user",
            "content": answer,
        })
