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
        workspace_dir: str = "",
        memory_dir: str = "./data/memory",
        state_dir: str = "./data/state",
        trace_dir: str = "./data/traces",
        mcp_tool_resolver: Callable | None = None,
        call_timeout: float | None = 120.0,
        session_timeout: float | None = None,
        session_mode_manager: SessionModeManager | None = None,
    ):
        self.loop_strategy = None  # removed — kept for compat during migration
        self.gate = GateChecker(max_turns=max_turns)
        self.state_store = state_store
        self.tool_executor = tool_executor
        self.event_bus = event_bus
        self._call_model = call_model
        self._stream_model = stream_model
        self._cancel_event = cancel_event
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._workspace_dir = workspace_dir
        self._memory_dir = memory_dir
        self._state_dir = state_dir
        self._trace_dir = trace_dir
        self._mcp_tool_resolver = mcp_tool_resolver
        self._call_timeout = call_timeout
        self._session_timeout = session_timeout
        self._session_mode_manager = session_mode_manager or SessionModeManager(global_mode=SessionMode.ASK)

        self._blocking = InProcessHookRunner(blocking_plugins or [])
        self._side = SubprocessHookRunner(side_plugins or [])
        self._interaction_round = 0

    def set_call_model(self, call_model) -> None:
        self._call_model = call_model

    def set_stream_model(self, stream_model) -> None:
        self._stream_model = stream_model

    def set_cancel_event(self, event: asyncio.Event) -> None:
        self._cancel_event = event

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

    async def _execute(self, state: AgentState):
        session_id = state.get("session_id", "default")
        self._current_session_id = session_id
        self._interaction_round = state.get("interaction_round", 0)
        state["interaction_round"] = self._interaction_round

        ctx = self._make_ctx(state, session_id, 0, "")

        # --- session_start (only on first call) ---
        if not state.get("_session_opened"):
            yield self._make_event("session_start", {"session_id": session_id}, session_id=session_id)
            try:
                await self._fire_blocking("session_start", ctx)
                await self._fire_side("session_start", ctx)
            except Exception as e:
                await self._handle_error(e, ctx)
            state["_session_opened"] = True

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
        while not aborted:
            if self._cancelled():
                state["_session_ended"] = True
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

            try:
                await self._fire_blocking("round_start", ctx)
                await self._fire_side("round_start", ctx)
            except Exception as e:
                decision = await self._handle_error(e, ctx)
                break  # abort on any error — don't retry round_start

            # --- turn loop ---
            while True:
                turn = state.get("current_turn", 0) + 1
                state["current_turn"] = turn

                # Gate check at top — bounds every iteration including retry/skip
                if self.gate.is_exceeded(current_turn=turn):
                    yield self._make_event(
                        "gate_exceeded",
                        {"reason": self.gate.reason, "current_turn": turn},
                        turn=turn, session_id=session_id,
                    )
                    break

                ctx = self._make_ctx(state, session_id, turn, "")

                # --- turn_start ---
                try:
                    await self._fire_blocking("turn_start", ctx)
                    await self._fire_side("turn_start", ctx)
                except Exception as e:
                    decision = await self._handle_error(e, ctx)
                    if decision.get("action") == "abort":
                        aborted = True
                        break
                    continue

                # --- pre_action: call_model ---
                ctx.current_step = "call_model"
                try:
                    async for event in self._fire_and_drain("pre_action", ctx):
                        yield event
                except Exception as e:
                    decision = await self._handle_error(e, ctx)
                    if decision.get("action") == "abort":
                        aborted = True
                        break
                    if decision.get("action") == "skip":
                        await self._fire_side("post_action", ctx)
                        continue

                # --- dispatch: model_call ---
                try:
                    async for event in self._action_call_model(state, ctx):
                        yield event
                except Exception as e:
                    decision = await self._handle_error(e, ctx)
                    action = decision.get("action", "abort")
                    if action == "abort":
                        aborted = True
                        break
                    elif action == "retry":
                        state["current_turn"] = turn - 1
                        await self.state_store.put(session_id, state)
                        continue
                    elif action == "skip":
                        await self._fire_side("post_action", ctx)
                        continue
                    elif action == "fallback":
                        await self.state_store.put(session_id, state)
                        continue
                    elif action == "rollback":
                        break
                    else:
                        break

                # Snapshot pending_tool_calls BEFORE execute_tools pops them
                has_tool_calls = bool(state.get("_pending_tool_calls"))

                # --- pre_action + dispatch: execute_tools (if model returned tool_calls) ---
                if has_tool_calls:
                    ctx.current_step = "execute_tools"
                    # Inject effective session mode into hook_data (absorbed from SessionModePlugin)
                    ctx.hook_data["effective_mode"] = self._session_mode_manager.resolve(None)
                    try:
                        async for event in self._fire_and_drain("pre_action", ctx):
                            yield event
                    except Exception as e:
                        decision = await self._handle_error(e, ctx)
                        if decision.get("action") == "abort":
                            aborted = True
                            break
                        if decision.get("action") == "skip":
                            await self._fire_side("post_action", ctx)
                            continue

                    try:
                        async for event in self._action_execute_tools(state, ctx):
                            yield event
                    except Exception as e:
                        decision = await self._handle_error(e, ctx)
                        action = decision.get("action", "abort")
                        if action == "abort":
                            aborted = True
                            break
                        elif action == "skip":
                            await self._fire_side("post_action", ctx)
                            continue
                        else:
                            break

                # --- post_dispatch ---
                try:
                    await self._fire_blocking("post_action", ctx)
                    await self._fire_side("post_action", ctx)
                except Exception as e:
                    decision = await self._handle_error(e, ctx)
                    if decision.get("action") == "abort":
                        aborted = True
                        break
                    continue

                # --- turn_end ---
                try:
                    await self._fire_blocking("turn_end", ctx)
                    await self._fire_side("turn_end", ctx)
                except Exception as e:
                    decision = await self._handle_error(e, ctx)
                    if decision.get("action") == "abort":
                        aborted = True
                        break
                    continue

                await self.state_store.put(session_id, state)

                # Text-only response (no tool_calls) → round complete
                if not has_tool_calls:
                    break

                # Gate check — terminate if budget exceeded
                if self.gate.is_exceeded(current_turn=turn):
                    yield self._make_event(
                        "gate_exceeded",
                        {"reason": self.gate.reason, "current_turn": turn},
                        turn=turn, session_id=session_id,
                    )
                    break

            # --- round_end ---
            try:
                await self._fire_blocking("round_end", ctx)
                await self._fire_side("round_end", ctx)
            except Exception as e:
                decision = await self._handle_error(e, ctx)
                if decision.get("action") == "abort":
                    break
                # Fall through to gate/exit checks below — round_end is the last
                # block in the loop, so continue would skip the break conditions.

            # Gate check at round level too
            if self.gate.is_exceeded(current_turn=state.get("current_turn", 0)):
                yield self._make_event(
                    "gate_exceeded",
                    {"reason": self.gate.reason, "current_turn": state.get("current_turn")},
                    turn=state.get("current_turn", 0), session_id=session_id,
                )
                break

            # No new user input after round — exit round loop
            msgs = state.get("messages", [])
            if msgs and msgs[-1].get("role") != "user":
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

        # Build messages — convert internal tool_calls format to API format
        msgs = self._to_api_messages(self._system_prompt, state.get("messages", []))

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
                    elif chunk.get("type") == "tool_call":
                        stream_tool_calls.append({
                            "id": chunk.get("id", ""),
                            "name": chunk.get("name", ""),
                            "params": json.loads(chunk.get("arguments", "{}")),
                        })
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
                if resp is None:
                    pass  # errored mid-stream, skip building resp
                else:
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

        yield self._make_event("model_call_end", {
            "model": model, "turn": turn,
            "content": resp.get("content", "") if isinstance(resp, dict) else "",
            "reasoning": resp.get("reasoning", "") if isinstance(resp, dict) else "",
            "usage": stream_usage,
        }, turn=turn, session_id=session_id)

        # Append assistant message
        content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        tool_calls = self._parse_tool_calls(resp)
        assistant_msg: dict = {"role": "assistant", "content": content}
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

        results = await self.tool_executor.execute(
            tool_calls, agent_mode="",
            engine=self, state_store=self.state_store,
            workspace_dir=self._workspace_dir,
        )

        for tc in tool_calls:
            r = results.get(tc.get("id", ""))
            tc_id = tc.get("id", "")
            yield self._make_event("tool_call_end", {
                "tool_name": tc.get("name", ""), "turn": turn, "id": tc_id,
                "success": r.success if r else False,
                "duration_ms": r.duration_ms if r else 0,
                "result": str(r.data)[:2000] if r and r.success and r.data else "",
                "error": str(r.error)[:2000] if r and r.error else "",
            }, turn=turn, session_id=session_id)

            content = str(r.data) if r and r.success else f"Error: {r.error}" if r and r.error else ""
            state["messages"].append({"role": "tool", "tool_call_id": tc["id"], "content": content})

        state["tool_results"] = {
            k: {"success": v.success, "data": v.data, "error": v.error}
            for k, v in results.items()
        }

        # Inject tool_call results for trace visibility
        for tc in tool_calls:
            r = results.get(tc.get("id", ""))
            ctx.inject_engine_event("tool_call_end", {
                "tool_name": tc.get("name", ""),
                "id": tc.get("id", ""),
                "params": tc.get("params", {}),
                "turn": turn,
                "success": r.success if r else False,
                "result": str(r.data)[:2000] if r and r.success and r.data else "",
                "error": str(r.error)[:500] if r and r.error else "",
                "duration_ms": r.duration_ms if r else 0,
            })

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
        ctx._event_ready = asyncio.Event()
        hook_task = asyncio.ensure_future(self._blocking.fire(step, ctx))

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

        while ctx._pending_events:
            evt = ctx._pending_events.pop(0)
            if self.event_bus:
                self.event_bus.emit(evt)
            yield evt

        if hook_task.exception():
            raise hook_task.exception()

        await self._fire_side(step, ctx)

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
            memory_dir=self._memory_dir,
            state_dir=self._state_dir,
            trace_dir=self._trace_dir,
            event_bus=self.event_bus,
        )

    @staticmethod
    def _to_api_messages(system_prompt: str, messages: list[dict]) -> list[dict]:
        """Convert internal message format to OpenAI API format.

        Internal tool_calls use {id, name, params} for convenience.
        API expects {id, type: "function", function: {name, arguments}}.
        Strips internal metadata fields (subtype, compactMetadata, isCompactSummary)
        from messages before sending to the API.
        """
        _STRIP_FIELDS = {"subtype", "compactMetadata", "isCompactSummary"}
        msgs = [{"role": "system", "content": system_prompt}]
        for m in messages:
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
            if role not in ("user", "assistant", "tool"):
                raise MessageContractError(f"Message {i} has invalid role: {role}")

        # First message must be user
        if msgs and msgs[0].get("role") != "user":
            raise MessageContractError("Messages must start with user role")

    async def _handle_error(self, exc: Exception, ctx: PluginContext) -> dict:
        """Fire error hook on blocking runner.

        Trace-captures every recovery decision. Raises SessionAbortedError
        when the decision is 'abort' so invoke()/astream() can clean up.
        Flushes pending trace events before abort so error evidence is
        persisted to JSONL.
        """
        ctx.hook_data["exception"] = exc
        try:
            await self._fire_blocking("error", ctx)
        except Exception as hook_err:
            self._emit_error_event(ctx, exc, f"error_hook_failed: {hook_err}")
            self._emit_decision_event(ctx, exc, "abort", "error_hook_failed")
            await self._flush_trace(ctx)
            raise SessionAbortedError(
                f"Error handler hook failed: {hook_err}"
            ) from exc
        decision = ctx.hook_data.get("_recovery_decision", {})
        if not decision:
            self._emit_error_event(ctx, exc, "no_recovery_decision")
            await self._flush_trace(ctx)
            raise  # re-raise original exception — no recovery strategy
        action = decision.get("action", "abort")
        reason = decision.get("reason", "")
        self._emit_decision_event(ctx, exc, action, reason)
        if action == "abort":
            await self._flush_trace(ctx)
            raise SessionAbortedError(
                f"Error handler decided abort: {exc}"
            ) from exc
        return decision

    def _emit_decision_event(self, ctx: PluginContext, exc: Exception,
                             action: str, reason: str) -> None:
        """Emit an error event capturing the error_handler decision for trace."""
        event = AgentEvent(
            type="error",
            data={
                "phase": ctx.current_step or "action",
                "detail": f"error_handler: {action} ({reason})",
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
                "action": action,
                "reason": reason,
            },
            session_id=ctx.session_id,
        )
        if self.event_bus:
            self.event_bus.emit(event)

    @staticmethod
    def _default_error_action(exc: Exception) -> dict:
        """Default recovery decision based on exception type."""
        name = type(exc).__name__
        if name in ("PermissionDenied", "ApprovalDenied", "SandboxViolation",
                     "ApprovalTimeout"):
            # Guard/approval blocked the tool — model should see the
            # tool_result and respond, not abort the turn.
            return {"action": "skip"}
        return {"action": "abort", "params": {"user_message": str(exc)}}

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

    def _emit_error_event(self, ctx: PluginContext, exc: Exception, detail: str) -> None:
        """Emit an error event to the trace — does not affect control flow."""
        event = AgentEvent(
            type="error",
            data={
                "phase": ctx.current_step,
                "exception": type(exc).__name__,
                "detail": detail,
                "message": str(exc)[:300],
            },
            session_id=ctx.session_id,
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
        injected by _emit_decision_event are persisted. Failure to flush
        is itself swallowed — we're already in an error path.
        """
        try:
            await self._fire_blocking("post_action", ctx)
            await self._fire_side("post_action", ctx)
        except Exception:
            pass

    def _cancelled(self) -> bool:
        return self._cancel_event is not None and self._cancel_event.is_set()

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

    async def astream(self, state: AgentState):
        session_id = state.get("session_id", "default")
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
