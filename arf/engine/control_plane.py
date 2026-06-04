"""ControlPlane — pure skeleton agent execution loop."""
import asyncio
import json
import logging
from typing import Any, Callable

from arf.core.state import AgentState
from arf.core.events import AgentEvent
from arf.core.protocols import LoopStrategy, StateStore, ToolExecutor, EventBus
from arf.core.plugin_context import PluginContext
from arf.hooks.in_process_runner import InProcessHookRunner
from arf.hooks.runner import SubprocessHookRunner

logger = logging.getLogger("arf.engine")


class ControlPlane:
    """Pure skeleton execution loop. All behavior is plugin-injected."""

    def __init__(
        self,
        *,
        loop_strategy: LoopStrategy,
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
    ):
        self.loop_strategy = loop_strategy
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

        self._blocking = InProcessHookRunner(blocking_plugins or [])
        self._side = SubprocessHookRunner(side_plugins or [])
        self._interaction_round = 0

    def set_call_model(self, call_model) -> None:
        self._call_model = call_model

    def set_stream_model(self, stream_model) -> None:
        self._stream_model = stream_model

    def set_cancel_event(self, event: asyncio.Event) -> None:
        self._cancel_event = event

    # ==================================================================
    # Core execution loop
    # ==================================================================

    async def _execute(self, state: AgentState):
        session_id = state.get("session_id", "default")
        self._interaction_round = state.get("interaction_round", 0) + 1
        state["interaction_round"] = self._interaction_round
        self.loop_strategy.max_turns = self._max_turns

        ctx = self._make_ctx(state, session_id, 0, "")
        yield self._make_event("session_start", {"session_id": session_id}, session_id=session_id)

        # --- session_start ---
        try:
            await self._fire_blocking("session_start", ctx)
            await self._fire_side("session_start", ctx)
        except Exception as e:
            await self._handle_error(e, ctx)
            # ErrorHandler may abort session

        # --- round loop ---
        while self.loop_strategy.should_continue(state):
            if self._cancelled():
                yield self._make_event("session_end", {"reason": "cancelled"}, session_id=session_id)
                break

            # --- round_start ---
            self._interaction_round += 1
            state["interaction_round"] = self._interaction_round
            ctx = self._make_ctx(state, session_id, 0, "")

            try:
                await self._fire_blocking("round_start", ctx)
                await self._fire_side("round_start", ctx)
            except Exception as e:
                decision = await self._handle_error(e, ctx)
                if decision.get("action") == "abort":
                    break
                continue

            # Check for strategy override from round_start hook
            strategy_override = ctx.hook_data.get("strategy")
            if strategy_override is not None:
                self.loop_strategy = strategy_override

            # --- turn loop ---
            while self.loop_strategy.should_continue(state):
                turn = state.get("current_turn", 0) + 1
                state["current_turn"] = turn
                step = self.loop_strategy.next_step(state)
                ctx = self._make_ctx(state, session_id, turn, step)

                # --- turn_start ---
                try:
                    await self._fire_blocking("turn_start", ctx)
                    await self._fire_side("turn_start", ctx)
                except Exception as e:
                    decision = await self._handle_error(e, ctx)
                    if decision.get("action") == "abort":
                        break
                    continue

                # --- pre_dispatch ---
                try:
                    async for event in self._fire_and_drain("pre_dispatch", ctx):
                        yield event
                except Exception as e:
                        decision = await self._handle_error(e, ctx)
                        if decision.get("action") == "abort":
                            break
                        if decision.get("action") == "skip":
                            continue

                # --- dispatch ---
                try:
                    async for event in self._dispatch(step, state, ctx):
                        yield event
                except Exception as e:
                    decision = await self._handle_error(e, ctx)
                    action = decision.get("action", "abort")
                    if action == "abort":
                        break
                    elif action == "retry":
                        state["current_turn"] = turn - 1
                        await self.state_store.put(session_id, state)
                        continue
                    elif action == "skip":
                        continue
                    elif action == "fallback":
                        await self.state_store.put(session_id, state)
                        continue
                    elif action == "rollback":
                        break
                    else:
                        break

                # --- post_dispatch ---
                try:
                    await self._fire_blocking("post_dispatch", ctx)
                    await self._fire_side("post_dispatch", ctx)
                except Exception as e:
                    decision = await self._handle_error(e, ctx)
                    if decision.get("action") == "abort":
                        break
                    continue

                # --- turn_end ---
                try:
                    await self._fire_blocking("turn_end", ctx)
                    await self._fire_side("turn_end", ctx)
                except Exception as e:
                    decision = await self._handle_error(e, ctx)
                    if decision.get("action") == "abort":
                        break
                    continue

                self.loop_strategy.on_transition("turn_end", ctx)
                await self.state_store.put(session_id, state)

                if self.loop_strategy.should_break(state):
                    break

                # Text-only response (no tool_calls) → round complete
                if step == "call_model" and not state.get("_pending_tool_calls"):
                    break

            # --- round_end ---
            try:
                await self._fire_blocking("round_end", ctx)
                await self._fire_side("round_end", ctx)
            except Exception as e:
                decision = await self._handle_error(e, ctx)
                if decision.get("action") == "abort":
                    break
                continue

            if self.loop_strategy.should_break(state):
                break

            # No new user input after round — exit round loop
            if state.get("messages", []) and state["messages"][-1].get("role") != "user":
                break

        # --- session_end ---
        ctx = self._make_ctx(state, session_id, state.get("current_turn", 0), "")
        try:
            await self._fire_blocking("session_end", ctx)
        except Exception:
            pass  # session_end failure should not prevent teardown
        await self._fire_side("session_end", ctx)
        await self.state_store.put(session_id, state)

        yield self._make_event("session_end", {"session_id": session_id}, session_id=session_id)

    # ==================================================================
    # Dispatch
    # ==================================================================

    async def _dispatch(self, step: str, state: AgentState, ctx: PluginContext):
        if step == "call_model":
            async for event in self._dispatch_call_model(state, ctx):
                yield event
        elif step == "execute_tools":
            async for event in self._dispatch_execute_tools(state, ctx):
                yield event

    async def _dispatch_call_model(self, state: AgentState, ctx: PluginContext):
        session_id = state.get("session_id", "default")
        turn = state.get("current_turn", 0)
        model = state.get("current_model", "")

        # Tool definitions via local MCP
        tools: list[dict] = []
        if self._mcp_tool_resolver:
            try:
                tools = await self._mcp_tool_resolver(state)
            except Exception:
                pass

        # Build messages — convert internal tool_calls format to API format
        msgs = self._to_api_messages(self._system_prompt, state.get("messages", []))

        # Validate message contract (check-only, throw on invalid)
        self._validate_messages(state)

        yield self._make_event("model_call_start", {"model": model, "turn": turn}, turn=turn, session_id=session_id)

        # Try streaming first, fall back to non-streaming
        resp = None
        stream_usage: dict = {}
        if self._stream_model:
            full_text = ""
            stream_tool_calls: list[dict] = []
            try:
                async for chunk in self._stream_model(msgs, model, tools=tools):
                    if chunk.get("type") == "chunk":
                        text = chunk.get("content", "")
                        reasoning = chunk.get("reasoning", "")
                        if text:
                            full_text += text
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
            resp = await self._call_model(msgs, model, tools=tools)

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

    async def _dispatch_execute_tools(self, state: AgentState, ctx: PluginContext):
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
                "result": str(r.data)[:500] if r and r.success and r.data else "",
                "error": str(r.error)[:500] if r and r.error else "",
            }, turn=turn, session_id=session_id)

            content = str(r.data) if r and r.success else f"Error: {r.error}" if r and r.error else ""
            state["messages"].append({"role": "tool", "tool_call_id": tc["id"], "content": content})

        state["tool_results"] = {
            k: {"success": v.success, "data": v.data, "error": v.error}
            for k, v in results.items()
        }

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
        )

    @staticmethod
    def _to_api_messages(system_prompt: str, messages: list[dict]) -> list[dict]:
        """Convert internal message format to OpenAI API format.

        Internal tool_calls use {id, name, params} for convenience.
        API expects {id, type: "function", function: {name, arguments}}.
        """
        msgs = [{"role": "system", "content": system_prompt}]
        for m in messages:
            if m.get("role") == "assistant" and "tool_calls" in m:
                converted = dict(m)
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
                converted["tool_calls"] = api_tcs
                msgs.append(converted)
            else:
                msgs.append(m)
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
        """Fire error hook on blocking runner."""
        ctx.hook_data["exception"] = f"{type(exc).__name__}: {exc}"
        try:
            await self._fire_blocking("error", ctx)
        except Exception as hook_err:
            self._emit_error_event(ctx, exc, f"error_hook_failed: {hook_err}")
            return self._default_error_action(exc)
        decision = ctx.hook_data.get("_recovery_decision", {})
        if not decision:
            self._emit_error_event(ctx, exc, "no_recovery_decision_defaulting_to_abort")
            return self._default_error_action(exc)
        return decision

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
        async for _ in self._execute(state):
            pass
        session_id = state.get("session_id", "default")
        if self.state_store:
            saved = await self.state_store.get(session_id)
            if saved:
                return saved
        return state

    async def astream(self, state: AgentState):
        async for event in self._execute(state):
            yield event


class MessageContractError(Exception):
    """Message list violates OpenAI chat API contract."""
