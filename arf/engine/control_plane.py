"""ControlPlane — pure skeleton agent execution loop."""
import asyncio
import json
import logging
from typing import Any, Callable

from arf.core.state import AgentState
from arf.core.events import AgentEvent
from arf.core.protocols import StateStore, ToolExecutor, EventBus
from arf.core.plugin_context import PluginContext
from arf.engine.gate import GateChecker
from arf.hooks.in_process_runner import InProcessHookRunner
from arf.hooks.runner import SubprocessHookRunner
from arf.session import SessionModeManager, SessionMode

logger = logging.getLogger("arf.engine")


class ControlPlane:
    """Pure skeleton execution loop. All behavior is plugin-injected."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        tool_executor: ToolExecutor,
        event_bus: EventBus | None = None,
        blocking_plugins: list | None = None,
        side_plugins: list | None = None,
        call_model: Callable | None = None,
        stream_model: Callable | None = None,
        cancel_event: asyncio.Event | None = None,
        system_prompt: str = "",
        max_turns: int = 50,
        max_tokens: int | None = None,  # None = no token budget limit
        window_size: int = 131072,
        workspace_dir: str = "",
        data_dir: str = "./data",
        memory_dir: str = "./data/memory",
        mcp_tool_resolver: Callable | None = None,
        call_timeout: float | None = 120.0,
        session_timeout: float | None = None,
        hitl_timeout: float = 300.0,
        session_mode_manager: SessionModeManager | None = None,
        hitl: "HITLProtocol | None" = None,
        task_lifecycle: "TaskLifecycleProtocol | None" = None,
        park_coordinator: "ParkCoordinator | None" = None,
    ):
        self.loop_strategy = None  # removed — kept for compat during migration
        self.gate = GateChecker(max_turns=max_turns, max_tokens=max_tokens)
        self.window_size = window_size
        self.state_store = state_store
        self.tool_executor = tool_executor
        self.event_bus = event_bus
        self._call_model = call_model
        self._stream_model = stream_model
        self._cancel_event = cancel_event
        self._system_prompt = system_prompt
        # Skill system
        self._skill_index = None  # set by set_skill_index()
        self._injected_system_msgs: list[dict] = []  # built at session start
        self._inventory_text = ""   # tool inventory, set by BaseAgent
        self._memory_index = None  # set by set_memory_index()
        self._max_turns = max_turns
        self._workspace_dir = workspace_dir
        self._data_dir = data_dir
        self._memory_dir = memory_dir
        self._mcp_tool_resolver = mcp_tool_resolver
        self._call_timeout = call_timeout
        self._session_timeout = session_timeout
        self._hitl_timeout = hitl_timeout
        self._session_mode_manager = session_mode_manager or SessionModeManager(global_mode=SessionMode.ASK)

        if hitl is None:
            from arf.core.protocols.hitl import DefaultHITL
            hitl = DefaultHITL(event_bus, state_store)
        if task_lifecycle is None:
            from arf.core.protocols.task_lifecycle import DefaultTaskLifecycle
            task_lifecycle = DefaultTaskLifecycle(event_bus)
        self._hitl = hitl
        self._task_lifecycle = task_lifecycle
        self._park_coordinator = park_coordinator

        self._blocking = InProcessHookRunner(blocking_plugins or [])
        self._side = SubprocessHookRunner(side_plugins or [])
        self._interaction_round = 0
        self._recovery_handlers: dict[str, Callable] = {
            "retry_turn":          self._recovery_retry_turn,
            "inject_tool_error":   self._recovery_inject_tool_error,
            "post_action_drain":   self._recovery_post_action_drain,
            "persist_state":       self._recovery_persist_state,
            "noop":                self._recovery_noop,
        }

    def set_call_model(self, call_model) -> None:
        self._call_model = call_model

    def set_stream_model(self, stream_model) -> None:
        self._stream_model = stream_model

    def set_skill_index(self, skill_index) -> None:
        """Inject the SkillIndex for use_skill tool and system-reminder."""
        self._skill_index = skill_index
        import arf.skills.use_skill_tool as use_skill_mod
        use_skill_mod._index = skill_index

    def set_context_texts(self, inventory: str = "") -> None:
        """Set tool inventory for injected system messages."""
        self._inventory_text = inventory

    def set_memory_index(self, memory_index) -> None:
        """Inject the MemoryIndex for memory layers + secrets tools."""
        self._memory_index = memory_index

    def set_undo_plugin(self, undo_plugin) -> None:
        """Inject undo plugin for round-level checkpoint + rollback."""
        self._undo_plugin = undo_plugin

    def undo(self, steps: int, session_id: str = "",
             workspace_dir: str = "") -> dict | None:
        """Roll back N rounds. Delegates to UndoPlugin's RoundManager."""
        if not hasattr(self, "_undo_plugin") or self._undo_plugin is None:
            return None
        return self._undo_plugin.undo(steps, session_id=session_id,
                                      workspace_dir=workspace_dir or self._workspace_dir)

    def checkpoint_count(self) -> int:
        """Number of available undo checkpoints."""
        if not hasattr(self, "_undo_plugin") or self._undo_plugin is None:
            return 0
        return self._undo_plugin.checkpoint_count()

    # ==================================================================
    # Session policy (was SessionModePlugin — absorbed into ControlPlane)
    # ==================================================================

    @property
    def session_mode(self) -> SessionMode:
        """Current global session mode (auto/ask/plan)."""
        return self._session_mode_manager.global_mode

    async def set_session_mode(self, mode: SessionMode | str, session_id: str = "") -> None:
        """Switch session policy at runtime, emitting session_policy_switch event.

        The app layer can consume session_policy_switch events to trigger
        UI transitions. The event carries the new mode and allows the app
        to echo back confirmation via external input.
        """
        if isinstance(mode, str):
            mode = SessionMode(mode)
        old_mode = self._session_mode_manager.global_mode
        if mode == old_mode:
            return
        self._session_mode_manager.set_global(mode)
        event = self._make_event("session_policy_switch", {
            "mode": mode.value,
            "previous_mode": old_mode.value,
        }, session_id=session_id)
        if self.event_bus:
            self.event_bus.emit(event)
        logger.info("Session policy switched: %s → %s (session=%s)", old_mode.value, mode.value, session_id)

    def resolve_effective_mode(self, agent_policy=None) -> SessionMode:
        """Resolve effective permission mode for the current agent."""
        return self._session_mode_manager.resolve(agent_policy)

    # ==================================================================
    # Core execution loop
    # ==================================================================

    async def _execute(self, state: AgentState, stop_on_text: bool = False):
        session_id = state.get("session_id", "default")
        self._current_session_id = session_id
        self._interaction_round = state.get("interaction_round", 0)
        state["interaction_round"] = self._interaction_round

        ctx = self._make_ctx(state, session_id, 0, "")

        # --- session_start (only on first call) ---
        if not state.get("_session_opened"):
            yield self._make_event("session_start", {"session_id": session_id}, session_id=session_id)
            ctx.hook_data["_error_phase"] = "session_start"
            try:
                await self._fire_blocking("session_start", ctx)
                await self._fire_side("session_start", ctx)
            except Exception as e:
                await self._dispatch_error(e, state, ctx)
            state["_session_opened"] = True
            state.setdefault("_task_start_round", 0)

            # Build injected system messages: skills → tools → memory
            self._injected_system_msgs = []
            if self._skill_index is not None:
                skill_md = self._skill_index.format_index_markdown()
                if skill_md:
                    self._injected_system_msgs.append(
                        {"role": "system", "content": skill_md})
            if self._inventory_text:
                self._injected_system_msgs.append(
                    {"role": "system", "content": self._inventory_text})

            # Inject memory layers (project, user, secrets)
            if self._memory_index is not None:
                mem_msgs = self._memory_index.build_injected_messages()
                self._injected_system_msgs.extend(mem_msgs)

        # --- user_input (once per astream call) ---
        messages = state.get("messages", [])
        for m in reversed(messages):
            if m.get("role") == "user":
                yield self._make_event("user_input", {
                    "content": m.get("content", ""),
                }, session_id=session_id)
                break

        # --- round loop ---
        aborted = False
        completed = False
        while not aborted and not completed:
            if self._cancelled():
                if not state.get("_session_ended"):
                    state["_session_ended"] = True
                    ctx.inject_engine_event("session_cancelled", {"reason": "cancelled"})
                    yield self._make_event("session_end", {"reason": "cancelled"}, session_id=session_id)
                break

            # --- round_start ---
            self._interaction_round += 1
            state["interaction_round"] = self._interaction_round
            ctx = self._make_ctx(state, session_id, 0, "")

            # Inject user_input for this round (dedup by message count)
            messages = state.get("messages", [])
            user_count = sum(1 for m in messages if m.get("role") == "user")
            if user_count > state.get("_last_injected_user_count", 0):
                state["_last_injected_user_count"] = user_count
                for m in reversed(messages):
                    if m.get("role") == "user":
                        ctx.inject_engine_event("user_input", {
                            "content": m.get("content", ""),
                        })
                        break

            ctx.hook_data["_error_phase"] = "round_start"
            try:
                await self._fire_blocking("round_start", ctx)
                await self._fire_side("round_start", ctx)
            except Exception as e:
                if await self._dispatch_error(e, state, ctx):
                    break
                continue

            # --- turn loop ---
            primitive = None
            while True:
                turn = state.get("current_turn", 0) + 1
                state["current_turn"] = turn

                # Gate check at top — bounds every iteration including retry/skip
                if self.gate.is_exceeded(
                    current_turn=turn,
                    total_tokens=state.get("_total_tokens", 0),
                ):
                    ctx.inject_engine_event("gate_check", {
                        "limit": self.gate.reason,
                        "current_turn": turn,
                        "max_turns": self.gate.max_turns,
                        "total_tokens": state.get("_total_tokens", 0),
                        "max_tokens": self.gate.max_tokens,
                        "exceeded": True,
                    })
                    yield self._make_event(
                        "gate_exceeded",
                        {"reason": self.gate.reason, "current_turn": turn},
                        turn=turn, session_id=session_id,
                    )
                    break

                ctx = self._make_ctx(state, session_id, turn, "")

                # --- turn_start ---
                ctx.hook_data["_error_phase"] = "turn_start"
                try:
                    await self._fire_blocking("turn_start", ctx)
                    await self._fire_side("turn_start", ctx)
                except Exception as e:
                    if await self._dispatch_error(e, state, ctx):
                        aborted = True
                        break
                    continue

                # --- pre_action: call_model ---
                ctx.current_step = "call_model"
                ctx.hook_data["_error_phase"] = "pre_action"
                logger.debug("cp pre_action ENTER sid=%s round=%s turn=%s", session_id, self._interaction_round, turn)
                try:
                    async for event in self._fire_and_drain("pre_action", ctx):
                        yield event
                except Exception as e:
                    logger.exception("DEBUG cp pre_action ERROR sid=%s: %s", session_id, e)
                    if await self._dispatch_error(e, state, ctx):
                        aborted = True
                        break
                    continue
                logger.debug("cp pre_action EXIT sid=%s round=%s turn=%s", session_id, self._interaction_round, turn)

                # --- dispatch: model_call ---
                ctx.hook_data["_error_phase"] = "model_call"
                logger.debug("cp model_call ENTER sid=%s round=%s turn=%s", session_id, self._interaction_round, turn)
                try:
                    async for event in self._action_call_model(state, ctx):
                        yield event
                except Exception as e:
                    if await self._dispatch_error(e, state, ctx):
                        aborted = True
                        break
                    continue

                # Snapshot pending_tool_calls BEFORE execute_tools pops them
                pending_tool_calls = list(state.get("_pending_tool_calls", []))
                has_tool_calls = bool(pending_tool_calls)
                ctx.inject_engine_event("turn_decision", {
                    "has_tool_calls": has_tool_calls,
                    "pending_tools": [t.get("name", "") for t in pending_tool_calls],
                })

                # --- pre_action + dispatch: execute_tools (if model returned tool_calls) ---
                if has_tool_calls:
                    ctx.current_step = "execute_tools"
                    # Inject effective session mode into hook_data (absorbed from SessionModePlugin)
                    ctx.hook_data["effective_mode"] = self._session_mode_manager.resolve(None)
                    ctx.hook_data["_pending_tool_calls"] = pending_tool_calls
                    ctx.hook_data["_error_phase"] = "pre_action"
                    try:
                        async for event in self._fire_and_drain("pre_action", ctx):
                            yield event
                    except Exception as e:
                        if await self._dispatch_error(e, state, ctx):
                            aborted = True
                            break
                        continue

                    ctx.hook_data["_error_phase"] = "execute_tools"
                    try:
                        async for event in self._action_execute_tools(state, ctx):
                            yield event
                    except Exception as e:
                        if await self._dispatch_error(e, state, ctx):
                            aborted = True
                            break
                        continue

                # --- post_dispatch ---
                ctx.hook_data["_error_phase"] = "post_action"
                try:
                    await self._fire_blocking("post_action", ctx)
                    await self._fire_side("post_action", ctx)
                except Exception as e:
                    if await self._dispatch_error(e, state, ctx):
                        aborted = True
                        break
                    continue

                # --- turn_end ---
                ctx.hook_data["_error_phase"] = "turn_end"
                try:
                    await self._fire_blocking("turn_end", ctx)
                    await self._fire_side("turn_end", ctx)
                except Exception as e:
                    if await self._dispatch_error(e, state, ctx):
                        aborted = True
                        break
                    continue

                await self.state_store.put(session_id, state)

                # Text-only response (no tool_calls) → round complete
                if not has_tool_calls:
                    ctx.inject_engine_event("turn_exit", {"reason": "no_tool_calls"})
                    if stop_on_text:
                        completed = True
                    break

                # Check for primitive signal — exit turn loop
                primitive = state.pop("_primitive_result", None)
                if primitive:
                    break

                # Gate check — terminate if budget exceeded
                if self.gate.is_exceeded(
                    current_turn=turn,
                    total_tokens=state.get("_total_tokens", 0),
                ):
                    ctx.inject_engine_event("gate_check", {
                        "limit": self.gate.reason,
                        "current_turn": turn,
                        "max_turns": self.gate.max_turns,
                        "total_tokens": state.get("_total_tokens", 0),
                        "max_tokens": self.gate.max_tokens,
                        "exceeded": True,
                    })
                    yield self._make_event(
                        "gate_exceeded",
                        {"reason": self.gate.reason, "current_turn": turn},
                        turn=turn, session_id=session_id,
                    )
                    break

            # --- round_end ---
            ctx.hook_data["_error_phase"] = "round_end"
            round_end_error_recovered = False
            try:
                await self._fire_blocking("round_end", ctx)
                await self._fire_side("round_end", ctx)
            except Exception as e:
                if await self._dispatch_error(e, state, ctx):
                    break
                ctx.inject_engine_event("round_end_warning", {
                    "detail": f"round_end hook error recovered: {e}",
                })
                round_end_error_recovered = True
                # Fall through to gate/exit checks below

            # -- task_completed hook (after round_end, if triggered) --
            if primitive == "task_completed":
                await self._fire_task_completed_hook(ctx, state)
                state["_task_start_round"] = ctx.interaction_round + 1

            # Gate check at round level too
            if self.gate.is_exceeded(
                current_turn=state.get("current_turn", 0),
                total_tokens=state.get("_total_tokens", 0),
            ):
                ctx.inject_engine_event("gate_check", {
                    "limit": self.gate.reason,
                    "current_turn": state.get("current_turn", 0),
                    "max_turns": self.gate.max_turns,
                    "total_tokens": state.get("_total_tokens", 0),
                    "max_tokens": self.gate.max_tokens,
                    "exceeded": True,
                })
                yield self._make_event(
                    "gate_exceeded",
                    {"reason": self.gate.reason, "current_turn": state.get("current_turn")},
                    turn=state.get("current_turn", 0), session_id=session_id,
                )
                break

            # No new user/system input after round — park and wait for
            # conditions registered by plugins (HITL / subagent / peer).
            msgs = state.get("messages", [])
            if msgs and msgs[-1].get("role") not in ("user", "system"):
                if self._park_coordinator is not None:
                    parked = await self._park_coordinator.park_round(
                        state, self._cancel_event,
                    )
                    if parked is None:
                        ctx.inject_engine_event("round_exit", {
                            "reason": "no_pending_conditions",
                        })
                        break
                    ctx.inject_engine_event("park_resolved", {
                        "wait_id": parked,
                    })
                    continue  # back to round loop → new round_start

                # Fallback: legacy fire_blocking for plugins that still
                # register session_park hooks directly.
                park_ctx = self._make_ctx(state, session_id, state.get("current_turn", 0), "")
                park_ctx.hook_data["_park_timeout"] = state.get("_park_timeout", None)
                park_ctx.hook_data["_hitl"] = self._hitl
                park_ctx.hook_data["_cancel_event"] = self._cancel_event
                park_ctx.hook_data["_data_dir"] = str(self._data_dir)
                await self._fire_blocking("session_park", park_ctx)

                # Re-check — legacy plugins may have injected new messages
                msgs = state.get("messages", [])
                if msgs and msgs[-1].get("role") in ("user", "system"):
                    ctx.inject_engine_event("round_continued", {
                        "reason": "session_park_injected",
                        "last_role": msgs[-1].get("role"),
                    })
                    continue

                ctx.inject_engine_event("round_exit", {
                    "reason": "no_user_input",
                    "last_message_role": msgs[-1].get("role") if msgs else "N/A",
                })
                break

        # Save state for continuation (session_end emitted by close())
        await self.state_store.put(session_id, state)

    # ==================================================================
    # Actions
    # ==================================================================

    async def _action_call_model(self, state: AgentState, ctx: PluginContext):
        session_id = state.get("session_id", "default")
        turn = state.get("current_turn", 0)
        model = state.get("current_model", "")

        # Tool definitions via local MCP
        # Wrapped in a hard timeout so a hung MCP subprocess doesn't
        # block the entire model call. MCP failures are non-fatal —
        # the model proceeds without tool definitions.
        tools: list[dict] = []
        if self._mcp_tool_resolver:
            try:
                tools = await asyncio.wait_for(
                    self._mcp_tool_resolver(state), timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.error("MCP tool resolution timed out after 5s")
                self._emit_mcp_error_event(
                    session_id, TimeoutError("MCP tool resolution timed out"))
            except Exception as e:
                logger.exception("MCP tool resolution failed")
                self._emit_mcp_error_event(session_id, e)

        # Kernel tools — always available
        if self._skill_index is not None:
            tools.append({
                "name": "kernel__use_skill",
                "description": (
                    "Load a Skill's full domain knowledge. "
                    "Call with the skill name to get detailed instructions, "
                    "conventions, and best practices for a specific task type. "
                    "Available skills are listed in the system reminder."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The name of the skill to load.",
                        },
                    },
                    "required": ["name"],
                },
            })

        # ask_user kernel tool — always available for HITL
        tools.append({
            "name": "kernel__ask_user",
            "description": (
                "Request a human decision. Use when you cannot proceed "
                "without human input. Your round will end, and the human's "
                "answer will be available in your next round as a new message."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question for the human.",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of choices. Empty = free-text answer.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Why human input is needed (background context).",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "ID of the task this question belongs to.",
                    },
                },
                "required": ["question"],
            },
        })

        # kernel__task_complete — always available for task completion
        tools.append({
            "name": "kernel__task_complete",
            "description": (
                "Signal that the current task is complete. Call this when "
                "you have finished the user's request. Provide a result "
                "summary, confidence (0.0-1.0), and optional notes. "
                "After calling, your round ends and a task_completed hook "
                "fires for memory extraction and task archiving."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": "Summary of what was accomplished.",
                    },
                    "files_changed": {
                        "type": "object",
                        "description": ("Files added/modified/deleted: "
                                        '{"added":[],"modified":[],"deleted":[]}.'),
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence in task completion, 0.0-1.0. Default 1.0.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Additional notes for auditing.",
                    },
                },
                "required": [],
            },
        })

        # kernel__search_task_memory — search past task experience
        if self._memory_index is not None:
            tools.append({
                "name": "kernel__search_task_memory",
                "description": (
                    "Search past task experience for relevant approaches "
                    "and pitfalls. Use this to learn from previous similar "
                    "tasks before starting new work. Provide a query "
                    "describing what you want to learn about."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What kind of past experience to search for (e.g. 'auth module refactoring', 'redis connection timeout').",
                        },
                    },
                    "required": ["query"],
                },
            })

        # Memory write tools — always available when memory index is present
        if self._memory_index is not None:
            tools.extend([
                {
                    "name": "kernel__write_project_memory",
                    "description": (
                        "Persist project-level memory that agents share across sessions. "
                        "Write architecture decisions, conventions, bug fixes, "
                        "design patterns, or important project context that all "
                        "team members should know. Content is Markdown."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Markdown content to persist as project memory.",
                            },
                        },
                        "required": ["content"],
                    },
                },
                {
                    "name": "kernel__write_user_memory",
                    "description": (
                        "Persist user-level memory that agents share about the user. "
                        "Write user preferences, decisions, knowledge, working style, "
                        "or constraints discovered during conversation. Content is Markdown."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Markdown content to persist as user memory.",
                            },
                        },
                        "required": ["content"],
                    },
                },
            ])

        # Secrets tools — available if memory index has a secrets store
        if self._memory_index is not None:
            try:
                secrets_enabled = getattr(
                    getattr(self._memory_index, '_cfg', None),
                    'secrets', None)
                if secrets_enabled is not None and secrets_enabled.enabled:
                    tools.extend([
                        {
                            "name": "read_secret",
                            "description": "Read an encrypted secret value by name. Use when you need an API key, password, or token.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "The secret name to read.",
                                    },
                                },
                                "required": ["name"],
                            },
                        },
                        {
                            "name": "write_secret",
                            "description": "Store a new encrypted secret or update an existing one. Only call when the user explicitly asks you to store a secret.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Variable name for the secret (e.g. DB_PASSWORD).",
                                    },
                                    "note": {
                                        "type": "string",
                                        "description": "What this secret is used for.",
                                    },
                                    "content": {
                                        "type": "string",
                                        "description": "The secret value to encrypt and store (password, token, key, etc.).",
                                    },
                                },
                                "required": ["name", "content"],
                            },
                        },
                        {
                            "name": "list_secrets",
                            "description": "List all available secret names (values are NOT shown).",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    ])
            except Exception:
                pass

        # Apply tool blacklist from state (depth limit enforcement)
        blacklist = state.get("_tool_blacklist", [])
        if blacklist:
            tools = [t for t in tools if t.get("name") not in blacklist]

        # Build messages — convert internal tool_calls format to API format
        msgs = self._to_api_messages(
            self._system_prompt, state.get("messages", []),
            injected_msgs=self._injected_system_msgs,
        )

        # Validate message contract (check-only, throw on invalid)
        self._validate_messages(state)

        yield self._make_event("model_call_start", {"model": model, "turn": turn}, turn=turn, session_id=session_id)
        ctx.inject_engine_event("model_call_start", {"model": model, "turn": turn})

        # Try streaming first, fall back to non-streaming
        resp = None
        stream_usage: dict = {}
        if self._stream_model:
            full_text = ""
            full_reasoning = ""
            stream_tool_calls: list[dict] = []
            try:
                async for chunk in self._stream_model(msgs, model, tools=tools):
                    if chunk.get("type") == "chunk":
                        text = chunk.get("content", "")
                        reasoning = chunk.get("reasoning", "")
                        if text:
                            full_text += text
                        if reasoning:
                            full_reasoning += reasoning
                        if text or reasoning:
                            yield self._make_event(
                                "thinking_delta",
                                {"content": text, "reasoning": reasoning},
                                turn=turn, session_id=session_id,
                            )
                    elif chunk.get("type") in ("tool_call", "tool_call_chunk"):
                        # Deduplicate by id — streaming yields incremental
                        # "tool_call_chunk" events followed by a final "tool_call"
                        # event for the same call. Update in-place if exists.
                        tc_id = chunk.get("id", "")
                        tc_name = chunk.get("name", "")
                        is_final = chunk.get("type") == "tool_call"
                        existing = None
                        for tc in stream_tool_calls:
                            if tc.get("id") == tc_id:
                                existing = tc
                                break
                        if is_final:
                            params = json.loads(chunk.get("arguments", "{}"))
                            if existing:
                                existing["name"] = tc_name or existing["name"]
                                existing["params"] = params
                                existing.pop("_raw_args", None)
                            else:
                                stream_tool_calls.append({
                                    "id": tc_id, "name": tc_name, "params": params,
                                })
                        else:
                            if existing:
                                existing["name"] = tc_name or existing["name"]
                                existing["_raw_args"] = chunk.get("arguments", "{}")
                            else:
                                stream_tool_calls.append({
                                    "id": tc_id, "name": tc_name,
                                    "_raw_args": chunk.get("arguments", "{}"),
                                })
                        # Yield tool_call_chunk so frontend can show progress
                        if not is_final:
                            yield self._make_event(
                                "tool_call_chunk",
                                {"id": tc_id, "name": tc_name,
                                 "arguments": chunk.get("arguments", "{}"),
                                 "delta": chunk.get("delta", "")},
                                turn=turn, session_id=session_id,
                            )
                    elif chunk.get("type") == "error":
                        yield self._make_event("error", {
                            "phase": "stream_model",
                            "code": chunk.get("code", 0),
                            "detail": chunk.get("detail", ""),
                            "note": "API returned error via stream, falling back to non-streaming",
                        }, turn=turn, session_id=session_id)
                        resp = None  # force non-streaming fallback
                        break
                    elif chunk.get("type") == "usage":
                        stream_usage = {
                            "prompt_tokens": chunk.get("prompt_tokens", 0),
                            "completion_tokens": chunk.get("completion_tokens", 0),
                            "total_tokens": chunk.get("total_tokens", 0),
                        }
                # Build resp from streamed data if streaming produced anything.
                # resp stays None only on explicit error or truly empty stream,
                # both of which fall back to non-streaming below.
                # Normalize tool calls: parse _raw_args from tool_call_chunk fallback
                for tc in stream_tool_calls:
                    if "params" not in tc and "_raw_args" in tc:
                        try:
                            tc["params"] = json.loads(tc.pop("_raw_args"))
                        except (json.JSONDecodeError, TypeError):
                            tc["params"] = {}
                    tc.pop("_raw_args", None)
                if full_text or full_reasoning or stream_tool_calls:
                    resp = {
                        "content": full_text,
                        "reasoning": full_reasoning,
                        "tool_calls": stream_tool_calls,
                        "usage": stream_usage,
                    }
            except Exception as e:
                yield self._make_event("error", {
                    "phase": "stream_model",
                    "detail": str(e)[:200],
                    "note": "Streaming crashed, falling back to non-streaming",
                }, turn=turn, session_id=session_id)

        if resp is None and self._call_model:
            resp = await (
                asyncio.wait_for(
                    self._call_model(msgs, model, tools=tools),
                    timeout=self._call_timeout,
                ) if self._call_timeout
                else self._call_model(msgs, model, tools=tools)
            )

        if resp is None:
            yield self._make_event("error", {
                "phase": "call_model",
                "detail": "Both streaming and non-streaming model calls failed",
                "model": model,
            }, turn=turn, session_id=session_id)
            return

        if not stream_usage:
            stream_usage = resp.get("usage", {}) if isinstance(resp, dict) else {}

        if stream_usage and stream_usage.get("total_tokens", 0) > 0:
            state["last_token_usage"] = stream_usage["total_tokens"]
            state["_total_tokens"] = state.get("_total_tokens", 0) + stream_usage["total_tokens"]

        yield self._make_event("model_call_end", {
            "model": model, "turn": turn,
            "content": resp.get("content", "") if isinstance(resp, dict) else "",
            "reasoning": resp.get("reasoning", "") if isinstance(resp, dict) else "",
            "usage": stream_usage,
        }, turn=turn, session_id=session_id)

        # Context stats for frontend dashboard
        yield self._make_event("context_stats", {
            "prompt_tokens": stream_usage.get("prompt_tokens", 0),
            "session_tokens": state.get("_total_tokens", 0),
            "window_size": self.window_size,
            "round": state.get("interaction_round", 0),
            "turn": turn,
        }, turn=turn, session_id=session_id)

        # Append assistant message
        content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        tool_calls = self._parse_tool_calls(resp)
        reasoning = resp.get("reasoning", "") if isinstance(resp, dict) else ""
        assistant_msg: dict = {"role": "assistant", "content": content}
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        state["messages"].append(assistant_msg)
        state["_pending_tool_calls"] = tool_calls

        # Inject model_call result for trace visibility
        ctx.inject_engine_event("model_call_end", {
            "model": model,
            "turn": turn,
            "content": content,
            "reasoning": resp.get("reasoning", "") if isinstance(resp, dict) else "",
            "tool_calls": [
                {"name": tc.get("name", ""), "params": tc.get("params", {})}
                for tc in tool_calls
            ],
            "usage": stream_usage,
        })

    async def _action_execute_tools(self, state: AgentState, ctx: PluginContext):
        """Execute tool calls, allow tool_output hooks to transform results, commit."""
        session_id = state.get("session_id", "default")
        turn = state.get("current_turn", 0)
        tool_calls = state.pop("_pending_tool_calls", [])

        if not tool_calls:
            return

        for tc in tool_calls:
            yield self._make_event("tool_call_start", {
                "tool_name": tc.get("name", ""), "turn": turn,
                "id": tc.get("id", ""),
                "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False),
            }, turn=turn, session_id=session_id)
            ctx.inject_engine_event("tool_call_start", {
                "tool_name": tc.get("name", ""),
                "id": tc.get("id", ""),
                "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False),
                "turn": turn,
            })

        # -- Execute tools --
        results = await self.tool_executor.execute(
            tool_calls, agent_mode="",
            engine=self, state_store=self.state_store,
            workspace_dir=self._workspace_dir,
        )

        # -- Store raw results in hook_data for tool_output hooks --
        raw_results: dict[str, dict] = {}
        for tc in tool_calls:
            r = results.get(tc.get("id", ""))
            raw_results[tc["id"]] = {
                "tool_name": tc.get("name", ""),
                "success": r.success if r else False,
                "data": str(r.data) if r and r.success and r.data else "",
                "error": str(r.error) if r and r.error else "",
                "duration_ms": r.duration_ms if r else 0,
                "turn": turn,
            }
        ctx.hook_data["_raw_tool_results"] = raw_results

        # -- Fire tool_output hook: plugins can modify _raw_tool_results --
        ctx.hook_data["_error_phase"] = "tool_output"
        try:
            await self._fire_blocking("tool_output", ctx)
            await self._fire_side("tool_output", ctx)
        except Exception:
            # Results remain in _raw_tool_results; commit with raw data on error
            pass

        # -- Commit: use hook-modified results --
        for tc in tool_calls:
            r = ctx.hook_data["_raw_tool_results"].get(tc["id"], {})
            tc_id = tc.get("id", "")

            yield self._make_event("tool_call_end", {
                "tool_name": r.get("tool_name", ""), "turn": turn, "id": tc_id,
                "success": r.get("success", False),
                "duration_ms": r.get("duration_ms", 0),
                "result": r.get("data", ""),
                "error": r.get("error", ""),
            }, turn=turn, session_id=session_id)

            content = r.get("data", "") if r.get("success") else f"Error: {r.get('error', '')}"
            state["messages"].append({"role": "tool", "tool_call_id": tc["id"], "content": content})

        state["tool_results"] = {
            tc_id: {"success": d["success"], "data": d["data"], "error": d["error"]}
            for tc_id, d in raw_results.items()
        }

        # Inject trace events (with hook-modified data)
        for tc in tool_calls:
            r = ctx.hook_data["_raw_tool_results"].get(tc["id"], {})
            ctx.inject_engine_event("tool_call_end", {
                "tool_name": r.get("tool_name", ""),
                "id": tc.get("id", ""),
                "params": tc.get("params", {}),
                "turn": turn,
                "success": r.get("success", False),
                "result": r.get("data", ""),
                "error": r.get("error", ""),
                "duration_ms": r.get("duration_ms", 0),
            })

        # -- Primitive detection --
        primitive = await self._detect_primitives(state, ctx, raw_results)
        if primitive:
            if primitive == "pending_human":
                decision = state.get("_pending_human_decision", {})
                yield self._make_event("need_human_input", {
                    "question": decision.get("question", ""),
                    "options": decision.get("options", []),
                    "context": decision.get("context", ""),
                    "task_id": decision.get("task_id", ""),
                }, session_id=ctx.session_id)
            state["_primitive_result"] = primitive

    # ==================================================================
    # Primitive detection: task_complete + HITL
    # ==================================================================

    async def _detect_primitives(
        self, state: dict, ctx: PluginContext, raw_results: dict
    ) -> str | None:
        """Check tool results for pending or task_complete primitives."""
        import json as _json

        for r in raw_results.values():
            data = r.get("data", "{}")
            if isinstance(data, str):
                try:
                    data = _json.loads(data)
                except (_json.JSONDecodeError, TypeError):
                    continue
            if not isinstance(data, dict):
                continue

            if data.get("pending"):
                request_result = await self._hitl.request_input(
                    question=data.get("question", ""),
                    options=data.get("options", []),
                    context=data.get("context", ""),
                    task_id=data.get("task_id", ""),
                    deadline=self._compute_hitl_deadline(),
                    ctx=ctx,
                )
                ctx.inject_engine_event("need_human_input", {
                    "request_id": request_result["request_id"],
                    "question": data.get("question", ""),
                    "options": data.get("options", []),
                    "context": data.get("context", ""),
                    "task_id": data.get("task_id", ""),
                    "deadline": self._compute_hitl_deadline(),
                })
                return "pending_human"

            if data.get("task_complete"):
                await self._task_lifecycle.complete(
                    result=data.get("result", ""),
                    files_changed=data.get("files_changed", {}),
                    confidence=data.get("confidence", 1.0),
                    notes=data.get("notes", ""),
                    ctx=ctx,
                )
                ctx.inject_engine_event("task_completed", {
                    "session_id": ctx.session_id,
                    "start_round": state.get("_task_start_round", 0),
                    "finish_round": ctx.interaction_round,
                    "result": data.get("result", ""),
                    "confidence": data.get("confidence", 1.0),
                    "notes": data.get("notes", ""),
                })
                state["_task_completion_data"] = {
                    "result": data.get("result", ""),
                    "confidence": data.get("confidence", 1.0),
                    "notes": data.get("notes", ""),
                }
                return "task_completed"

        return None

    def _compute_hitl_deadline(self) -> float:
        import time as _time
        return _time.time() + self._hitl_timeout

    async def _fire_task_completed_hook(
        self, ctx: PluginContext, state: dict
    ) -> None:
        completion_data = state.pop("_task_completion_data", {})
        hook_ctx = self._make_ctx(
            state, ctx.session_id, state.get("current_turn", 0), "",
        )
        hook_ctx.hook_data.update({
            "session_id": ctx.session_id,
            "start_round": state.get("_task_start_round", 0),
            "finish_round": ctx.interaction_round,
            "task_result": completion_data.get("result", ""),
            "notes": completion_data.get("notes", ""),
            "confidence": completion_data.get("confidence", 1.0),
        })
        await self._fire_side("task_completed", hook_ctx)

    # ==================================================================
    # Helpers
    # ==================================================================

    async def _fire_and_drain(self, step: str, ctx: PluginContext):
        """Fire blocking hook, yielding events as they are emitted by hooks.

        Hooks call ctx.emit() to push events into the stream. While the
        hook is blocked (e.g. waiting for approval), we wait. Drained
        events are emitted to both stream (yield) and trace (event_bus).
        """
        import asyncio
        logger.debug("_fire_and_drain ENTER step=%s sid=%s current_step=%s", step, ctx.session_id, ctx.current_step)
        ctx._event_ready = asyncio.Event()
        hook_task = asyncio.ensure_future(self._blocking.fire(step, ctx))
        logger.debug("_fire_and_drain hook_task created step=%s", step)

        while not hook_task.done():
            evt_wait = asyncio.ensure_future(ctx._event_ready.wait())
            await asyncio.wait(
                [hook_task, evt_wait], return_when=asyncio.FIRST_COMPLETED)
            evt_wait.cancel()
            while ctx._pending_events:
                evt = ctx._pending_events.pop(0)
                if self.event_bus:
                    self.event_bus.emit(evt)
                yield evt
            ctx._event_ready.clear()

        logger.debug("_fire_and_drain hook_task DONE step=%s exception=%s", step, hook_task.exception())
        while ctx._pending_events:
            evt = ctx._pending_events.pop(0)
            if self.event_bus:
                self.event_bus.emit(evt)
            yield evt

        if hook_task.exception():
            raise hook_task.exception()

        await self._fire_side(step, ctx)
        logger.debug("_fire_and_drain EXIT step=%s sid=%s", step, ctx.session_id)

    def _make_ctx(self, state, session_id, turn, step) -> PluginContext:
        return PluginContext(
            session_id=session_id,
            interaction_round=self._interaction_round,
            turn=turn,
            current_step=step,
            state=state,
            messages=state.get("messages", []),
            tool_definitions=[],
            system_prompt=self._system_prompt,
            model=state.get("current_model", ""),
            workspace_dir=self._workspace_dir,
            data_dir=self._data_dir,
            memory_dir=self._memory_dir,
            event_bus=self.event_bus,
        )

    @staticmethod
    def _to_api_messages(system_prompt: str, messages: list[dict],
                         injected_msgs: list[dict] | None = None) -> list[dict]:
        """Convert internal message format to OpenAI API format.

        System messages structure:
          [0] system_prompt (from agent.yaml, identity + hard rules)
          [1..N] injected_msgs (skills → tools → memory, each a system msg)

        Internal tool_calls use {id, name, params} for convenience.
        API expects {id, type: "function", function: {name, arguments}}.
        Strips internal metadata fields from messages before sending.
        """
        _STRIP_FIELDS = {"subtype", "compactMetadata", "isCompactSummary"}
        msgs: list[dict] = []
        # Layer 0: system prompt
        msgs.append({"role": "system", "content": system_prompt})
        # Injected system messages: skills, tools, memory (each section independently)
        for im in (injected_msgs or []):
            if im.get("content"):
                msgs.append({"role": "system", "content": im["content"]})
        # Collect system messages from state — plugins may inject
        # system messages dynamically (e.g. a2a_teammates team context).
        # These are converted to API system messages so the model sees them.
        for m in messages:
            if m.get("role") == "system" and m.get("content"):
                msgs.append({"role": "system", "content": m["content"]})
        # User/assistant/tool messages
        for m in messages:
            if m.get("role") == "system":
                continue
            cleaned = {k: v for k, v in m.items() if k not in _STRIP_FIELDS}
            if m.get("role") == "assistant" and "tool_calls" in m:
                api_tcs = []
                for tc in m["tool_calls"]:
                    api_tcs.append({
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False),
                        },
                    })
                cleaned["tool_calls"] = api_tcs
            msgs.append(cleaned)
        return msgs

    def _validate_messages(self, state: AgentState) -> None:
        """Check message contract. Throw on violation — ErrorHandler repairs."""
        msgs = state.get("messages", [])
        if not msgs:
            return

        for i, m in enumerate(msgs):
            if not isinstance(m, dict):
                raise MessageContractError(f"Message {i} is not a dict: {type(m)}")
            role = m.get("role", "")
            if role not in ("user", "assistant", "tool", "system"):
                raise MessageContractError(f"Message {i} has invalid role: {role}")

        # First message must be user
        if msgs and msgs[0].get("role") != "user":
            raise MessageContractError("Messages must start with user role")

    async def _handle_error(self, exc: Exception, ctx: PluginContext) -> dict:
        """Fire error hook. Returns recovery decision or raises.

        Injects error_dispatch events via ctx.inject_engine_event on ALL
        paths so TracePlugin captures them in the JSONL trace — even when
        the error_handler decides retry and the turn loop recreates ctx.
        """
        ctx.hook_data["exception"] = exc
        try:
            await self._fire_blocking("error", ctx)
        except Exception as hook_err:
            ctx.inject_engine_event("error_dispatch", {
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
                "decision": "fatal",
                "reason": f"error_hook_failed: {hook_err}",
            })
            await self._fire_side("error", ctx)
            await self._flush_trace(ctx)
            raise SessionAbortedError(
                f"Error handler hook failed: {hook_err}"
            ) from exc

        decision = ctx.hook_data.get("_recovery_decision")
        if not decision:
            ctx.inject_engine_event("error_dispatch", {
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
                "decision": "fatal",
                "reason": "no_recovery_decision",
            })
            await self._fire_side("error", ctx)
            await self._flush_trace(ctx)
            raise SessionAbortedError(
                f"No recovery strategy: {exc}"
            ) from exc

        reason = decision.get("reason", "")
        ctx.inject_engine_event("error_dispatch", {
            "exception": type(exc).__name__,
            "message": str(exc)[:300],
            "decision": decision.get("recovery", "unknown"),
            "reason": reason,
        })
        await self._fire_side("error", ctx)
        return decision

    async def _dispatch_error(self, exc: Exception, state: dict, ctx: PluginContext) -> bool:
        """Unified error dispatch. Returns True if the loop should break."""
        try:
            decision = await self._handle_error(exc, ctx)
        except Exception:
            # _handle_error already injected error_dispatch and fired side
            # hooks before raising — this catch is a safety net only.
            return True

        recovery = decision.get("recovery", "")
        if recovery:
            handler = self._recovery_handlers.get(recovery)
            if handler:
                try:
                    await handler(state, ctx, decision.get("params", {}))
                except Exception as handler_exc:
                    logger.warning(
                        "Recovery handler '%s' failed: %s. Original error: %s",
                        recovery, handler_exc, exc)
                    ctx.inject_engine_event("error_dispatch", {
                        "exception": type(exc).__name__,
                        "message": str(exc)[:300],
                        "decision": "fatal",
                        "reason": f"recovery handler '{recovery}' failed: {handler_exc}",
                    })
                    await self._fire_side("error", ctx)
                    return True  # handler failure → break, don't mask original error
            else:
                logger.warning("No recovery handler registered for '%s'", recovery)
        return False

    # ==================================================================
    # Recovery handlers
    # ==================================================================

    async def _recovery_noop(self, state: dict, ctx: PluginContext, params: dict) -> None:
        """No-op recovery — take no action."""
        pass

    async def _recovery_retry_turn(self, state: dict, ctx: PluginContext, params: dict) -> None:
        """Decrement current_turn and persist so the turn loop retries."""
        state["current_turn"] = max(0, state.get("current_turn", 1) - 1)
        session_id = state.get("session_id", "default")
        await self.state_store.put(session_id, state)

    async def _recovery_persist_state(self, state: dict, ctx: PluginContext, params: dict) -> None:
        """Persist the current state to the state store."""
        session_id = state.get("session_id", "default")
        await self.state_store.put(session_id, state)

    async def _recovery_inject_tool_error(self, state: dict, ctx: PluginContext, params: dict) -> None:
        """Inject tool error results into messages so the model sees them."""
        error_text = params.get("error", "Tool execution failed")
        for tc in (ctx.hook_data.get("_pending_tool_calls") or []):
            state.setdefault("messages", []).append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": f"Error executing tool '{tc.get('name', '')}': {error_text}",
            })

    async def _recovery_post_action_drain(self, state: dict, ctx: PluginContext, params: dict) -> None:
        """Fire side hooks for post_action to drain pending trace events."""
        await self._fire_side("post_action", ctx)

    def _emit_mcp_error_event(self, session_id: str, exc: Exception) -> None:
        """Emit MCP error to trace — MCP failures should be visible."""
        event = AgentEvent(
            type="error",
            data={
                "phase": "mcp_tool_resolution",
                "detail": f"MCP tool resolution failed: {exc}",
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
            },
            session_id=session_id,
        )
        if self.event_bus:
            self.event_bus.emit(event)

    async def _fire_blocking(self, event_type: str, ctx: PluginContext) -> None:
        self._blocking.update_runtime(
            session_id=ctx.session_id, interaction_round=ctx.interaction_round)
        await self._blocking.fire(event_type, ctx)

    async def _fire_side(self, event_type: str, ctx: PluginContext) -> None:
        self._side.update_runtime(
            session_id=ctx.session_id, interaction_round=ctx.interaction_round)
        await self._side.fire(event_type, ctx)

    async def _flush_trace(self, ctx: PluginContext) -> None:
        """Fire post_action hooks to flush pending trace events to JSONL.

        Called before raising SessionAbortedError so error decision events
        are persisted. Failure to flush is itself swallowed — we're already
        in an error path.
        """
        try:
            await self._fire_blocking("post_action", ctx)
            await self._fire_side("post_action", ctx)
        except Exception:
            pass

    def _cancelled(self) -> bool:
        return self._cancel_event is not None and self._cancel_event.is_set()

    def set_cancel_event(self, event: asyncio.Event) -> None:
        """Wire an external cancel_event for cascade interrupt.

        When set, _cancelled() returns True and the engine exits at the
        next round boundary.
        """
        self._cancel_event = event

    def _parse_tool_calls(self, response) -> list[dict]:
        if isinstance(response, dict):
            return response.get("tool_calls", [])
        return []

    def _make_event(self, type: str, data: dict, turn: int = 0,
                    session_id: str = "", emit: bool = True) -> AgentEvent:
        data["round"] = self._interaction_round
        event = AgentEvent(type=type, data=data, turn=turn, session_id=session_id)
        if emit and self.event_bus:
            self.event_bus.emit(event)
        return event

    # ==================================================================
    # Public API
    # ==================================================================

    async def invoke(self, state: AgentState) -> AgentState:
        session_id = state.get("session_id", "default")
        try:
            try:
                if self._session_timeout:
                    await asyncio.wait_for(
                        self._consume_execute(state), timeout=self._session_timeout
                    )
                else:
                    async for _ in self._execute(state):
                        pass
            except (SessionAbortedError, asyncio.TimeoutError):
                state["session_active"] = False
                state["_aborted"] = True
                state["_error"] = "Session aborted via error_handler decision or timeout"
                if self.state_store:
                    await self.state_store.put(session_id, state)
            else:
                # invoke() is a one-shot session — auto-close
                await self._consume_close(state)
        except Exception as exc:
            # Unknown error — no recovery strategy, save state and re-raise
            # so the caller (chat/astream consumer) can see the real exception.
            state["session_active"] = False
            state["_aborted"] = True
            state["_error"] = str(exc)
            logging.getLogger("arf").exception(
                "invoke() session %s failed with unhandled error", session_id)
            if self.state_store:
                await self.state_store.put(session_id, state)
            raise

        if self.state_store:
            saved = await self.state_store.get(session_id)
            if saved:
                return saved
        return state

    async def _consume_execute(self, state: AgentState):
        """Coroutine wrapper — consumes _execute events for asyncio.wait_for."""
        async for _ in self._execute(state):
            pass

    async def astream(self, state: AgentState, stop_on_text: bool = False):
        session_id = state.get("session_id", "default")
        try:
            try:
                async for event in self._execute(state, stop_on_text=stop_on_text):
                    yield event
            except SessionAbortedError:
                state["_session_ended"] = True
                state["session_active"] = False
                state["_aborted"] = True
                state["_error"] = "Session aborted via error_handler decision"
                if self.state_store:
                    await self.state_store.put(session_id, state)
                yield self._make_event(
                    "session_end",
                    {"session_id": session_id, "reason": "aborted"},
                    session_id=session_id,
                )
            except GeneratorExit:
                # Client disconnected — persist state for recovery.
                # Must NOT yield; GeneratorExit forbids it.
                state["_session_ended"] = True
                state["session_active"] = False
                state["_aborted"] = True
                state["_error"] = "Client disconnected"
                if self.state_store:
                    try:
                        await self.state_store.put(session_id, state)
                    except Exception:
                        pass
                return
            else:
                if not state.get("_session_ended"):
                    state["_session_ended"] = True
                    state["session_active"] = False
                    if self.state_store:
                        await self.state_store.put(session_id, state)
                    yield self._make_event(
                        "session_end",
                        {"session_id": session_id, "reason": "completed"},
                        session_id=session_id,
                    )
        except Exception as exc:
            # Unknown error — no recovery strategy, propagate to caller
            state["_session_ended"] = True
            state["session_active"] = False
            state["_aborted"] = True
            state["_error"] = str(exc)
            logging.getLogger("arf").exception(
                "astream() session %s failed with unhandled error", session_id)
            if self.state_store:
                await self.state_store.put(session_id, state)
            yield self._make_event(
                "session_end",
                {"session_id": session_id, "reason": "error", "error": str(exc)},
                session_id=session_id,
            )
            raise

    async def resume(self, state: AgentState):
        """Resume execution from saved state. No new user message appended.

        Differs from astream(): does not require a user_message arg.
        Restores turn/interaction_round counters from *state*, then enters
        the execute loop directly. session_start preamble is skipped because
        _session_opened is already True in the saved state.

        Yields AgentEvent like astream().
        """
        session_id = state.get("session_id", "default")
        self._current_session_id = session_id
        self._interaction_round = state.get("interaction_round", 0)
        state["interaction_round"] = self._interaction_round

        try:
            try:
                async for event in self._execute(state):
                    yield event
            except SessionAbortedError:
                state["_session_ended"] = True
                state["session_active"] = False
                state["_aborted"] = True
                state["_error"] = "Session aborted via error_handler decision"
                if self.state_store:
                    await self.state_store.put(session_id, state)
                yield self._make_event(
                    "session_end",
                    {"session_id": session_id, "reason": "aborted"},
                    session_id=session_id,
                )
            except GeneratorExit:
                # Client disconnected — persist state for recovery.
                # Must NOT yield; GeneratorExit forbids it.
                state["_session_ended"] = True
                state["session_active"] = False
                state["_aborted"] = True
                state["_error"] = "Client disconnected"
                if self.state_store:
                    try:
                        await self.state_store.put(session_id, state)
                    except Exception:
                        pass
                return
            else:
                if not state.get("_session_ended"):
                    state["_session_ended"] = True
                    state["session_active"] = False
                    if self.state_store:
                        await self.state_store.put(session_id, state)
                    yield self._make_event(
                        "session_end",
                        {"session_id": session_id, "reason": "completed"},
                        session_id=session_id,
                    )
        except Exception as exc:
            state["_session_ended"] = True
            state["session_active"] = False
            state["_aborted"] = True
            state["_error"] = str(exc)
            logging.getLogger("arf").exception(
                "resume() session %s failed with unhandled error", session_id)
            if self.state_store:
                await self.state_store.put(session_id, state)
            yield self._make_event(
                "session_end",
                {"session_id": session_id, "reason": "error", "error": str(exc)},
                session_id=session_id,
            )
            raise

    async def close(self, state: AgentState):
        """Emit session_end + fire hooks + save state. Idempotent.

        Call once per conversation. After close(), the session is marked
        inactive. Subsequent astream()/invoke() calls with the same
        session_id will start a new session.
        """
        if state.get("_session_ended"):
            return
        state["_session_ended"] = True
        state["session_active"] = False

        # Signal cancel_event to interrupt any in-progress session_park wait
        if self._cancel_event is not None and not self._cancel_event.is_set():
            self._cancel_event.set()

        session_id = state.get("session_id", "default")
        ctx = self._make_ctx(state, session_id, state.get("current_turn", 0), "")
        try:
            await self._fire_blocking("session_end", ctx)
        except Exception:
            pass  # session_end failure should not prevent teardown
        await self._fire_side("session_end", ctx)
        await self.state_store.put(session_id, state)

        yield self._make_event("session_end", {"session_id": session_id}, session_id=session_id)

    async def _consume_close(self, state: AgentState):
        """Coroutine wrapper — consumes close() events for invoke()."""
        async for _ in self.close(state):
            pass


class SessionAbortedError(Exception):
    """Fatal — error_handler decided abort. Propagated to invoke() for cleanup."""


class MessageContractError(Exception):
    """Message list violates OpenAI chat API contract."""
