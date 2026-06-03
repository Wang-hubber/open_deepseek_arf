"""GraphEngine — DI-driven Agent execution loop."""
import asyncio
import copy
import json
import time
import random
import logging
logger = logging.getLogger("arf.engine")
from collections import deque
from pathlib import Path
from typing import Any, Callable
from arf.core.protocols import (
    LoopStrategy, StateStore, ToolExecutor, Planner,
    ToolResolver, MemoryStore, MemoryRetriever, MemoryWriter, HookRunner,
    GuardRunner, EventBus, ErrorPolicy, ModelRouter, CompactionStrategy,
)
from arf.core.plugin_context import PluginContext
from arf.core.state import AgentState, TurnContext
from arf.core.events import AgentEvent
from arf.compaction.sliding_window import DEFAULT_WINDOW_SIZE

# Recovery: continue message for max_tokens truncation
_CONTINUE_MESSAGE = (
    "Output limit hit. Continue directly from where you stopped. "
    "Do not restart, do not summarize, do not repeat what was already said."
)


def _backoff_delay(attempt: int, base: float = 1.0, max_delay: float = 30.0) -> float:
    """Exponential backoff with jitter for transient transport errors."""
    return min(base * (2 ** attempt), max_delay) + random.uniform(0, 1)


class _ToolExecutable:
    """Adapter: wraps a tool call as an Executable for ActionRunner/Promotion."""

    def __init__(
        self, name: str, params: dict[str, Any], tool_call_id: str,
        dependencies: list[str] | None = None,
        resources: list[str] | None = None,
        side_effect: bool = True,
        retry_policy: "RetryPolicy | None" = None,
        timeout: float | None = None,
        engine: "GraphEngine | None" = None,
    ):
        from arf.core.execution import RetryPolicy as RP
        self.name = name
        self.kind = "tool"
        self.dependencies = dependencies or []
        self.resources = resources or []
        self.side_effect = side_effect
        self.retry_policy = retry_policy or RP()
        self.timeout = timeout
        self._params = params
        self._id = tool_call_id
        self._engine = engine

    async def execute(self) -> "ExecuteResult":
        import time as _time
        from arf.core.execution import ExecuteResult, ExecutionError
        start = _time.monotonic()
        try:
            if self._engine is None:
                return ExecuteResult(name=self.name, success=False,
                    error=ExecutionError(kind="deterministic", message="no engine reference"))
            results = await self._engine.tool_executor.execute(
                [{"name": self.name, "params": self._params, "id": self._id}],
                agent_mode="",
                engine=self._engine,
                state_store=self._engine.state_store,
                workspace_dir=getattr(self._engine, '_workspace_dir', ''),
            )
            r = results.get(self._id)
            if r and r.success:
                return ExecuteResult(name=self.name, success=True, data=r.data,
                    duration_ms=(_time.monotonic() - start) * 1000)
            error_msg = str(r.error) if r and r.error else "tool execution failed"
            # Classify error: tool-level errors are deterministic
            return ExecuteResult(name=self.name, success=False,
                error=ExecutionError(kind="deterministic", message=error_msg),
                duration_ms=(_time.monotonic() - start) * 1000)
        except Exception as exc:
            return ExecuteResult(name=self.name, success=False,
                error=ExecutionError(kind="transient",
                    message=f"{type(exc).__name__}: {exc}"),
                duration_ms=(_time.monotonic() - start) * 1000)

    async def rollback(self) -> None:
        pass  # tool backends don't implement rollback yet


class GraphEngine:
    def __init__(
        self,
        *,
        loop_strategy: LoopStrategy,
        state_store: StateStore,
        tool_executor: ToolExecutor,
        tool_resolver: ToolResolver,
        planner: Planner | None = None,
        memory_store: MemoryStore | None = None,
        memory_retriever: MemoryRetriever | None = None,
        memory_writer: MemoryWriter | None = None,
        hook_runner: HookRunner | None = None,
        guard_runner: GuardRunner | None = None,
        event_bus: EventBus | None = None,
        error_policy: ErrorPolicy | None = None,
        model_router: ModelRouter | None = None,
        compaction: CompactionStrategy | None = None,
        plugin_runner: "InProcessHookRunner | None" = None,  # NEW: in-process plugin runner
        call_model: Callable | None = None,
        stream_model: Callable | None = None,
        cancel_event: asyncio.Event | None = None,
        system_prompt: str = "",
        max_turns: int = 50,
        max_undo_depth: int = 3,
        memory_max_tokens: int = 2000,
        memory_top_k: int = 5,
        approval_enabled: bool = False,
        approval_allowlist: list[str] | None = None,
        approval_timeout: float = 60.0,
        memory_workspace: str = "./memory",
        workspace_dir: str = "",
        recovery_config: "RecoveryConfig | None" = None,
        promotion: "Promotion | None" = None,
        action_runner: "ActionRunner | None" = None,
        session_mode_manager=None,
        main_permission_lists=None,
        main_agent_policy=None,
    ):
        self.loop_strategy = loop_strategy
        self.approval_enabled = approval_enabled
        self.approval_timeout = approval_timeout
        self._approval_allowlist: set[str] = set(approval_allowlist or [])
        self._pending_approvals: dict[str, asyncio.Event] = {}  # decision_id → set on approve
        self._approval_results: dict[str, bool] = {}  # decision_id → True/False
        self.state_store = state_store
        self.tool_executor = tool_executor
        self.tool_resolver = tool_resolver
        self.planner = planner
        self.memory_store = memory_store
        self.memory_retriever = memory_retriever
        self.memory_writer = memory_writer
        self.hook_runner = hook_runner
        self.guard_runner = guard_runner
        self.event_bus = event_bus
        self.error_policy = error_policy
        self.model_router = model_router
        self.compaction = compaction
        self.plugin_runner = plugin_runner
        self._call_model = call_model
        self._stream_model = stream_model
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._memory_max_tokens = memory_max_tokens
        self._memory_top_k = memory_top_k
        self._cancel_event = cancel_event
        self._interaction_round = 0
        self._memory_dir = memory_workspace
        self._workspace_dir = workspace_dir
        self._promotion = promotion
        self._action_runner = action_runner
        self._session_mode_manager = session_mode_manager
        self._main_permission_lists = main_permission_lists
        self._main_agent_policy = main_agent_policy
        # Recovery config with safe defaults
        if recovery_config is not None:
            self._recovery_config = recovery_config
        else:
            from arf.agent.config import RecoveryConfig
            self._recovery_config = RecoveryConfig()
        # Round-level checkpoint manager
        from arf.engine.round_manager import RoundManager
        self._rounds = RoundManager(max_undo_depth=max_undo_depth)
        # Tool progress streaming — tools write chunks here, SSE loop reads them
        import asyncio as _asyncio_queue
        self._tool_progress_queue: _asyncio_queue.Queue = _asyncio_queue.Queue()
        # Monotonic tool call ID counter for globally unique SSE event IDs
        self._tc_seq: int = 0

    def _next_tc_id(self, model_id: str) -> str:
        self._tc_seq += 1
        return f"tc{self._tc_seq}_{model_id}"

    @property
    def cancel_event(self) -> asyncio.Event | None:
        return self._cancel_event

    def set_cancel_event(self, event: asyncio.Event) -> None:
        """Late-binding: inject a cancellation token after construction."""
        self._cancel_event = event

    def approve(self, decision_id: str, approved: bool) -> bool:
        """Resolve a pending approval request. Returns True if the ID was found."""
        self._approval_results[decision_id] = approved
        evt = self._pending_approvals.pop(decision_id, None)
        if evt:
            evt.set()
            return True
        return False

    def undo(self, steps: int = 1, workspace_dir: str = "",
             session_id: str = "") -> AgentState | None:
        """Pop N rounds and restore state from the target checkpoint.

        Emits undo_executed trace event so consumers can mark the
        rollback boundary without deleting historical events.
        """
        active_trace = list(self._rounds.active_round.agent_trace) if self._rounds.active_round else []
        current_round = self._rounds.current_round_num
        target_round = max(0, current_round - steps)
        restored = self._rounds.undo(steps, workspace_dir)
        if restored is not None:
            self._emit("undo_executed", {
                "from_round": current_round,
                "to_round": target_round,
                "steps": steps,
                "agent_trace": active_trace,
            }, session_id=session_id or restored.get("session_id", "default"))
        return restored

    def checkpoint_count(self) -> int:
        return self._rounds.count()

    async def _try_repair_400(self, exc: Exception, state: AgentState,
                               msgs: list, system_prompt: str,
                               session_id: str, model: str, tools: list) -> tuple:
        """Try message repair for 400 tool-message errors. Returns (repaired_msgs, response_or_None)."""
        if "400" not in str(exc) or "tool" not in str(exc).lower():
            return msgs, None
        self._repair_messages(state)
        if self.state_store:
            await self.state_store.put(session_id, state)
        rebuilt = [{"role": "system", "content": system_prompt}]
        rebuilt.extend(state.get("messages", []))
        try:
            resp = await self._call_model(rebuilt, model, tools=tools)
            logger = logging.getLogger("arf.engine")
            logger.info("Message repair resolved 400 error, retry succeeded")
            return rebuilt, resp
        except Exception:
            return rebuilt, None

    def _resolve_fallback(self, model_name: str, exc: Exception) -> str | None:
        """Resolve fallback model via error_policy → model_router chain.

        Returns fallback model name, or None if fallback is not
        configured or unavailable.
        """
        if not self.error_policy or not self.model_router:
            return None
        try:
            action = self.error_policy.on_model_error(exc, model_name, 0)
        except Exception:
            return None
        if action.action != "fallback":
            return None
        return self.model_router.fallback_from(model_name)

    def _choose_recovery(
        self,
        stop_reason: str | None,
        error_text: str | None,
    ) -> "RecoveryDecision":
        """Classify an error/stop condition into a recovery decision.

        Called from invoke/astream at two points:
          (a) after response: check finish_reason for "length"
          (b) in except block: check exception text for transient/overflow
        """
        from arf.core.results import RecoveryDecision

        # Output truncated by max_tokens
        if stop_reason == "length":
            return RecoveryDecision(kind="continue", reason="output truncated at max_tokens")

        if error_text:
            el = error_text.lower()
            # Context overflow
            if ("prompt" in el and "long" in el) or ("context" in el and ("too" in el or "exceed" in el)):
                return RecoveryDecision(kind="compact", reason="context too large")

            # Transient transport failures
            # Note: "server error" is deliberately excluded — 5xx errors
            # go through the existing fallback chain (_resolve_fallback)
            # instead of retry-with-backoff.
            if any(w in el for w in ["timeout", "rate", "unavailable", "connection", "timed out"]):
                return RecoveryDecision(kind="backoff", reason="transient transport failure")

        return RecoveryDecision(kind="fail", reason="unknown or non-recoverable error")

    async def _apply_recovery(
        self,
        decision: "RecoveryDecision",
        state: dict[str, Any],
        msgs: list[dict],
        error: Exception | None = None,
    ) -> tuple[dict[str, Any], list[dict], bool]:
        """Apply a recovery decision. Returns (state, msgs, should_continue).

        should_continue=True means caller should 'continue' its loop.
        Raises the original error if budget is exhausted for backoff.
        """

        rs = state.setdefault("_recovery_state", {
            "continuation_attempts": 0,
            "compact_attempts": 0,
            "transport_attempts": 0,
        })

        if decision.kind == "continue":
            if rs["continuation_attempts"] >= self._recovery_config.max_continuation:
                self._emit("recovery_exhausted", {
                    "path": "continue",
                    "attempts": rs["continuation_attempts"],
                    "max": self._recovery_config.max_continuation,
                }, session_id=state.get("session_id", "default"))
                raise RuntimeError(
                    f"Recovery exhausted: max continuation attempts "
                    f"({self._recovery_config.max_continuation}) reached"
                )
            rs["continuation_attempts"] += 1
            logger.info("[Recovery] continue (attempt %s/%s)",
                        rs["continuation_attempts"], self._recovery_config.max_continuation)
            self._emit("recovery_continue", {
                "attempt": rs["continuation_attempts"],
                "max": self._recovery_config.max_continuation,
                "reason": decision.reason,
            }, session_id=state.get("session_id", "default"))
            # Persist continue message into state so it survives the loop restart
            state.setdefault("messages", []).append({"role": "user", "content": _CONTINUE_MESSAGE})
            msgs.append({"role": "user", "content": _CONTINUE_MESSAGE})
            return state, msgs, True

        if decision.kind == "compact":
            if rs["compact_attempts"] >= self._recovery_config.max_compaction:
                self._emit("recovery_exhausted", {
                    "path": "compact",
                    "attempts": rs["compact_attempts"],
                    "max": self._recovery_config.max_compaction,
                }, session_id=state.get("session_id", "default"))
                raise RuntimeError(
                    f"Recovery exhausted: max compaction attempts "
                    f"({self._recovery_config.max_compaction}) reached"
                )
            rs["compact_attempts"] += 1
            logger.info("[Recovery] compact (attempt %s/%s)",
                        rs["compact_attempts"], self._recovery_config.max_compaction)
            self._emit("recovery_compact", {
                "attempt": rs["compact_attempts"],
                "max": self._recovery_config.max_compaction,
                "reason": decision.reason,
            }, session_id=state.get("session_id", "default"))
            if self.compaction:
                state = await self.compaction.compact(state)
                state = self._repair_messages(state)
            return state, msgs, True

        if decision.kind == "backoff":
            if rs["transport_attempts"] >= self._recovery_config.max_transport_retry:
                self._emit("recovery_exhausted", {
                    "path": "backoff",
                    "attempts": rs["transport_attempts"],
                    "max": self._recovery_config.max_transport_retry,
                }, session_id=state.get("session_id", "default"))
                if error:
                    raise error
                raise RuntimeError(
                    f"Recovery exhausted: max transport retries "
                    f"({self._recovery_config.max_transport_retry}) reached"
                )
            rs["transport_attempts"] += 1
            import asyncio as _asyncio
            delay = _backoff_delay(
                rs["transport_attempts"],
                self._recovery_config.backoff_base,
                self._recovery_config.backoff_max,
            )
            logger.info("[Recovery] backoff %.1fs (attempt %s/%s)",
                        delay, rs["transport_attempts"], self._recovery_config.max_transport_retry)
            self._emit("recovery_backoff", {
                "attempt": rs["transport_attempts"],
                "max": self._recovery_config.max_transport_retry,
                "delay": delay,
                "reason": decision.reason,
            }, session_id=state.get("session_id", "default"))
            await _asyncio.sleep(delay)
            return state, msgs, True

        # fail — do nothing, let existing fallback chain handle it
        return state, msgs, False

    def _reset_recovery_state(self, state: dict[str, Any]) -> None:
        """Reset all recovery counters after a normal successful round.

        Called after tool execution completes normally — the agent made
        progress, so recovery budgets are refreshed for the next round.
        """
        old_state = state.get("_recovery_state", {})
        if any(old_state.get(k, 0) > 0 for k in ("continuation_attempts", "compact_attempts", "transport_attempts")):
            self._emit("recovery_reset", {
                "previous": dict(old_state),
            }, session_id=state.get("session_id", "default"))
        state["_recovery_state"] = {
            "continuation_attempts": 0,
            "compact_attempts": 0,
            "transport_attempts": 0,
        }

    def _cancelled(self) -> bool:
        """Check if execution has been cancelled (non-blocking)."""
        return self._cancel_event is not None and self._cancel_event.is_set()

    async def _resolve_tools_for_agent(self, state: AgentState) -> list[dict[str, object]]:
        """Get tool definitions from MCP — already aggregated and namespaced."""
        result: list[dict[str, object]] = []
        seen: set[str] = set()

        if self.tool_resolver:
            try:
                for td in await self.tool_resolver.get_tool_definitions(
                    self._last_user_message(state), top_k=50
                ):
                    td_name = td.get("name", "") if isinstance(td, dict) else getattr(td, "name", "")
                    if td_name and td_name not in seen:
                        seen.add(td_name)
                        if isinstance(td, dict):
                            result.append(td)
                        else:
                            result.append({
                                "name": td.name,
                                "description": td.description,
                                "parameters": td.parameters,
                            })
            except Exception:
                pass

        return result

    def set_call_model(self, call_model) -> None:
        """Late-binding injection of the model API call function."""
        self._call_model = call_model

    def set_stream_model(self, stream_model) -> None:
        """Late-binding injection of the streaming model API call function."""
        self._stream_model = stream_model

    def set_model_windows(self, windows: dict[str, int]) -> None:
        """Store model_name → context_window mapping for compaction decisions."""
        self._model_windows = windows

    def _emit(self, event_type: str, data: dict[str, object], session_id: str = "", agent_name: str = "") -> None:
        if self.event_bus:
            data["round"] = self._interaction_round
            self.event_bus.emit(AgentEvent(
                type=event_type, data=data, turn=data.get("turn", 0),
                session_id=session_id or data.get("session_id", ""),
                agent_name=agent_name or data.get("agent_name", ""),
            ))

    def _make_event(self, type: str, data: dict[str, object], turn: int = 0,
                    session_id: str = "", emit: bool = True) -> AgentEvent:
        """Create an AgentEvent and optionally publish to EventBus."""
        data["round"] = self._interaction_round
        event = AgentEvent(type=type, data=data, turn=turn, session_id=session_id)
        if emit and self.event_bus:
            self.event_bus.emit(event)
        return event

    def _close_tool_calls(self, state: AgentState) -> AgentState:
        """Deprecated. Use _repair_messages instead."""
        return self._repair_messages(state)

    def _repair_messages(self, state: AgentState) -> AgentState:
        """Rebuild message list to conform to the OpenAI chat API contract.

        Contract:
          - Messages must alternate: user, assistant, user, assistant, ...
          - System messages belong only in the top-level system prompt.
          - Every tool message must follow an assistant with matching tool_calls.
          - Every assistant with tool_calls must have matching tool results.
          - The sequence must start with a user message.

        Strategy: walk the list, keep only messages with valid roles, close
        open tool_calls, and discard orphaned tool messages.
        """
        raw: list = state.get("messages", [])
        if not raw:
            return state

        logger = logging.getLogger("arf.engine")
        orig_len = len(raw)

        # Phase 1: collect assistant → tool_calls map (by index in filtered result)
        # First pass: filter to valid roles and record positions
        keep: list[dict] = []
        assistant_tc_map: dict[int, list[dict]] = {}  # filtered_idx → tool_calls

        for m in raw:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "")
            if role == "system":
                continue  # system prompt is prepended separately
            if role not in ("user", "assistant", "tool"):
                continue
            idx = len(keep)
            keep.append(dict(m))  # shallow copy to avoid mutating original refs
            if role == "assistant" and m.get("tool_calls"):
                assistant_tc_map[idx] = list(m["tool_calls"])

        if not keep:
            return state

        # Ensure starts with user
        while keep and keep[0].get("role") != "user":
            removed = keep.pop(0)
            logger.warning("Repair: removed leading %s message (sequence must start with user)",
                           removed.get("role"))

        # Rebuild assistant_tc_map after removing leading messages
        # (indices shifted, entries for removed assistants must be dropped)
        assistant_tc_map = {}
        for i, m in enumerate(keep):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                assistant_tc_map[i] = list(m["tool_calls"])

        # Phase 2: for each assistant with tool_calls, ensure matching tool messages
        # exist immediately after it (before next user or assistant)
        to_remove: set[int] = set()
        for a_idx, tcs in sorted(assistant_tc_map.items()):
            # Find scope: from a_idx+1 to next assistant or user
            scope_end = len(keep)
            for j in range(a_idx + 1, len(keep)):
                if keep[j].get("role") in ("user", "assistant"):
                    scope_end = j
                    break

            # Collect existing tool coverage in scope (keep first, mark rest duplicate)
            covered: dict[str, int] = {}  # tc_id → first position
            for j in range(a_idx + 1, scope_end):
                if keep[j].get("role") == "tool":
                    tc_id = keep[j].get("tool_call_id", "")
                    if tc_id:
                        if tc_id in covered:
                            to_remove.add(j)  # duplicate → remove
                        else:
                            covered[tc_id] = j
            seen: set[str] = set(covered.keys())

            # Inject missing tool results
            for tc in tcs:
                tc_id = tc.get("id", "")
                if tc_id and tc_id not in seen:
                    logger.warning("Repair: injecting missing tool result for %s", tc_id)
                    # Insert right after the last tool message in scope, or after assistant
                    insert_at = scope_end
                    for j in range(a_idx + 1, scope_end):
                        if keep[j].get("role") == "tool":
                            insert_at = j + 1
                    keep.insert(insert_at, {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": "(tool result unavailable)",
                    })
                    # Shift indices
                    new_rm = {i + 1 if i >= insert_at else i for i in to_remove}
                    to_remove = new_rm
                    for k in list(assistant_tc_map.keys()):
                        if k >= insert_at:
                            assistant_tc_map[k + 1] = assistant_tc_map.pop(k)

        # Phase 3: remove orphaned tool messages (no matching assistant before them,
        # scoped to preceding assistant or user boundary)
        assistant_set = set(assistant_tc_map.keys())
        for i, m in enumerate(keep):
            if i in to_remove:
                continue
            if m.get("role") != "tool":
                continue
            tc_id = m.get("tool_call_id", "")
            if not tc_id:
                to_remove.add(i)
                continue
            # Search backward for matching assistant, stop at user boundary
            found = False
            for j in range(i - 1, -1, -1):
                prev_role = keep[j].get("role", "")
                if prev_role == "assistant":
                    for tc in keep[j].get("tool_calls", []):
                        if tc.get("id") == tc_id:
                            found = True
                            break
                    break  # first assistant backward is the matching scope
                if prev_role == "user":
                    break
            if not found:
                to_remove.add(i)

        for i in sorted(to_remove, reverse=True):
            logger.warning("Repair: removing invalid message at idx %d (role=%s)", i, keep[i].get("role"))
            del keep[i]

        if len(keep) != orig_len:
            logger.info("_repair_messages: %d → %d messages", orig_len, len(keep))
        state["messages"] = keep
        return state

    def _last_user_message(self, state: AgentState) -> str:
        for m in reversed(state.get("messages", [])):
            if m.get("role") == "user":
                return m.get("content", "")
        return ""

    def _inject_hook_messages(self, results, state: AgentState) -> None:
        """Check hook results for exit-code-2 injection messages."""
        if not results:
            return
        for r in results:
            if r.exit_code == 2 and r.injected_message:
                msg = r.injected_message.strip()
                if msg:
                    state["messages"].append({"role": "system", "content": f"[Hook: {r.hook_name}] {msg}"})

    def _pars_tool_calls(self, response) -> list[dict]:
        """Parse tool_calls from a model response (dict or string)."""
        if isinstance(response, dict):
            return response.get("tool_calls", [])
        if isinstance(response, str):
            import json
            try:
                data = json.loads(response)
                return data.get("tool_calls", [])
            except (json.JSONDecodeError, TypeError):
                pass
        return []

    async def _step_classify_tool_calls(
        self, state: AgentState, tool_calls: list[dict],
        turn: int, session_id: str,
        collect_approvals: bool = False,
    ) -> tuple[list[dict], list[tuple[str, str]], list[dict], list[dict]]:
        """Guard pipeline, sandbox, permissions, and approval for all tool calls.

        Returns (valid_calls, denied_calls, events, pending_approvals) where
        events is a list of dicts for the caller to emit via its own mechanism.

        When collect_approvals=True, approval events are returned in pending_approvals
        for the caller to emit BEFORE calling _wait_approvals. Otherwise (invoke
        path), approvals are waited inline and pending_approvals is empty.
        """
        events: list[dict] = []
        pending_approvals: list[dict] = []
        valid_calls: list[dict] = []
        denied_calls: list[tuple[str, str]] = []

        pipeline_data = state.get("active_pipeline")
        completed = set(pipeline_data.get("completed", [])) if pipeline_data else set()

        if not self.guard_runner and not self._promotion:
            return tool_calls, [], [], []

        for tc in tool_calls:
            name = tc.get("name", "")
            params = tc.get("params", {})

            if pipeline_data:
                steps = pipeline_data.get("steps", [])
                step_info = {s["tool"]: s.get("depends_on", []) for s in steps}
                if name in step_info:
                    missing = [d for d in step_info[name] if d not in completed]
                    if missing:
                        ready = [t for t, deps in step_info.items()
                                 if t not in completed and all(d in completed for d in deps)]
                        reason = (
                            f"pipeline: '{name}' requires {missing} to complete first. "
                            f"Ready: {ready}"
                        )
                        denied_calls.append((name, reason))
                        events.append({"type": "guard_block",
                                       "data": {"tool_name": name, "guard": "pipeline", "reason": reason}})
                        continue

            # --- Unified permission check via SessionModeManager ---
            agent_policy = getattr(self, '_main_agent_policy', None)

            from arf.session import SessionMode, has_side_effect
            effective_mode = SessionMode.ASK
            if self._session_mode_manager:
                effective_mode = self._session_mode_manager.resolve(agent_policy)

            if effective_mode == SessionMode.AUTO:
                perm_action = "allow"
                perm_reason = "auto mode"
            elif effective_mode == SessionMode.PLAN:
                if has_side_effect(name):
                    perm_action = "deny"
                    perm_reason = "plan mode: read-only, this tool has side effects"
                else:
                    perm_action = "allow"
                    perm_reason = "plan mode: read-only tool allowed"
            else:  # ASK
                if self.guard_runner:
                    perm_action = self.guard_runner.check_tool_permission(name, params)
                    perm_reason = "denied by config" if perm_action == "deny" else ""
                else:
                    perm_action = "allow"
                    perm_reason = ""

            if perm_action == "deny":
                denied_calls.append((name, perm_reason or "denied by permission config"))
                events.append({"type": "guard_block",
                               "data": {"tool_name": name, "guard": "permission",
                                        "reason": perm_reason or "denied by config"}})
                continue

            if perm_action == "ask":
                # Unified permission model: 'ask' list defines tools that
                # require user approval. approval_enabled is True when
                # permissions.ask is non-empty.
                needs_approval = self.approval_enabled and (
                    not self._approval_allowlist or name in self._approval_allowlist
                )
                if not needs_approval:
                    denied_calls.append((name, "requires approval (channel not enabled)"))
                    events.append({"type": "guard_block",
                                   "data": {"tool_name": name, "guard": "approval",
                                            "reason": "requires approval"}})
                    continue
                # --- approval required ---
                decision_id = f"{session_id}_{name}_{id(tc)}"
                if collect_approvals:
                    events.append({"type": "approval_required",
                                   "data": {"decision_id": decision_id, "tool_name": name,
                                            "params": params}})
                    pending_approvals.append({
                        "decision_id": decision_id, "tool_name": name, "params": params,
                    })
                    continue  # caller will _wait_approvals then re-classify
                # Inline wait (invoke path)
                self._emit("approval_required", {
                    "decision_id": decision_id, "tool_name": name, "params": params,
                }, session_id=session_id)
                approval_evt = asyncio.Event()
                self._pending_approvals[decision_id] = approval_evt
                try:
                    await asyncio.wait_for(approval_evt.wait(), timeout=self.approval_timeout)
                except asyncio.TimeoutError:
                    self._pending_approvals.pop(decision_id, None)
                    self._approval_results.pop(decision_id, None)
                    denied_calls.append((name, "approval timed out"))
                    events.append({"type": "approval_resolved",
                                   "data": {"decision_id": decision_id, "tool_name": name,
                                            "approved": False, "reason": "timeout"}})
                    continue
                approved = self._approval_results.pop(decision_id, False)
                if not approved:
                    denied_calls.append((name, "denied by user"))
                    events.append({"type": "approval_resolved",
                                   "data": {"decision_id": decision_id, "tool_name": name,
                                            "approved": False, "reason": "denied by user"}})
                    continue
                events.append({"type": "approval_resolved",
                               "data": {"decision_id": decision_id, "tool_name": name,
                                        "approved": True, "reason": "approved"}})
                valid_calls.append(tc)
            else:
                # allow — no approval needed
                events.append({"type": "guard_pass", "data": {"tool_name": name}})
                completed.add(name)
                valid_calls.append(tc)

        if pipeline_data:
            state["active_pipeline"]["completed"] = list(completed)

        return valid_calls, denied_calls, events, pending_approvals

    async def _wait_approvals(
        self, pending: list[dict], session_id: str,
    ) -> list[dict]:
        """Wait for each pending approval and return resolved events.

        Pre-registers ALL events before waiting, so that rapid user clicks
        (which arrive while we're still yielding earlier events) can find
        the decision_id in _pending_approvals.
        """
        # Pre-register all events first — the SSE stream already sent
        # approval_required events; the frontend may POST /approve at any moment.
        for pa in pending:
            self._pending_approvals.setdefault(pa["decision_id"], asyncio.Event())

        events: list[dict] = []
        for pa in pending:
            decision_id = pa["decision_id"]
            tool_name = pa["tool_name"]
            approval_evt = self._pending_approvals[decision_id]
            try:
                await asyncio.wait_for(approval_evt.wait(), timeout=self.approval_timeout)
            except asyncio.TimeoutError:
                self._pending_approvals.pop(decision_id, None)
                self._approval_results.pop(decision_id, None)
                events.append({"type": "approval_resolved",
                               "data": {"decision_id": decision_id, "tool_name": tool_name,
                                        "approved": False, "reason": "timeout"}})
                continue
            approved = self._approval_results.pop(decision_id, False)
            events.append({"type": "approval_resolved",
                           "data": {"decision_id": decision_id, "tool_name": tool_name,
                                    "approved": approved,
                                    "reason": "approved" if approved else "denied by user"}})
        return events

    # ═══════════════════════════════════════════════════════════════════
    # Unified execution path — _execute() is the single source of truth.
    # invoke() and astream() are thin wrappers.
    # ═══════════════════════════════════════════════════════════════════

    async def _step_call_model(self, state: AgentState):
        """Shared call_model step — handles streaming + non-streaming internally."""
        session_id = state.get("session_id", "default")
        turn = state.get("current_turn", 0) + 1
        state["current_turn"] = turn

        user_msg = self._last_user_message(state)
        yield self._make_event(type="user_input",
                         data={"content": user_msg, "turn": turn},
                         turn=turn, session_id=session_id)

        if not self._call_model:
            return

        # Model routing
        model = state["current_model"]
        if self.model_router:
            routed = await self.model_router.route(self._last_user_message(state), state.get("messages", []))
            model = routed or model
            state["current_model"] = model

        # Compaction
        if self.compaction:
            cd = state.get("_compaction_cooldown", 0)
            if cd > 0:
                state["_compaction_cooldown"] = cd - 1
            window = self._model_windows.get(model, DEFAULT_WINDOW_SIZE) if hasattr(self, '_model_windows') else DEFAULT_WINDOW_SIZE
            if self.compaction.should_compact(state, window_size=window):
                yield self._make_event(type="compaction_start",
                                 data={"turn": turn, "model": model, "msg_count": len(state.get("messages", []))},
                                 turn=turn, session_id=session_id)
                state = await self.compaction.compact(state)
                state = self._repair_messages(state)
                yield self._make_event(type="compaction_end",
                                 data={"turn": turn, "msg_count": len(state.get("messages", [])),
                                       "summary_len": len(state.get("context_summary", ""))},
                                 turn=turn, session_id=session_id)

        # Tool resolution
        tools = await self._resolve_tools_for_agent(state)
        self.loop_strategy.max_turns = self._max_turns

        # System prompt with per-turn placeholders
        system_prompt = self._system_prompt
        summary = state.get("context_summary", "")
        if summary:
            if "$MEMORY" in system_prompt:
                system_prompt = system_prompt.replace("$MEMORY", f"## Memory\n{summary}")
            else:
                system_prompt += f"\n\n## Memory\n{summary}"
        if getattr(self, '_workspace_dir', '') and "$WORKSPACE" in system_prompt:
            system_prompt = system_prompt.replace(
                "$WORKSPACE",
                f"## Workspace\nAll file operations are relative to `{self._workspace_dir}`. "
                "Use relative paths from this directory."
            )
        if "$TURN_BUDGET" in system_prompt:
            remaining = self.loop_strategy.max_turns - state.get("current_turn", 0)
            system_prompt = system_prompt.replace(
                "$TURN_BUDGET",
                f"## Turn Budget\n"
                f"You have {self.loop_strategy.max_turns} turns total, "
                f"{max(0, remaining)} remaining. "
                "Plan your work within this budget. "
                "When running low, compress: skip non-essentials, "
                "summarize partial progress."
            )

        state = self._repair_messages(state)
        msgs = [{"role": "system", "content": system_prompt}]
        msgs.extend(state.get("messages", []))

        if self.hook_runner:
            self.hook_runner.update_runtime(
                session_id=session_id, interaction_round=self._interaction_round)
            yield self._make_event(type="hook_start", data={"event": "pre_model_call", "turn": turn},
                             turn=turn, session_id=session_id)
            h_results = await self.hook_runner.fire("pre_model_call", {
                "messages": msgs, "model": model, "messages_count": len(msgs),
                "session_id": session_id})
            yield self._make_event(type="hook_end", data={"event": "pre_model_call", "turn": turn,
                             "count": len(h_results), "passed": sum(1 for r in h_results if r.exit_code == 0),
                             "failed": sum(1 for r in h_results if r.exit_code != 0)},
                             turn=turn, session_id=session_id)
            self._inject_hook_messages(h_results, state)

        yield self._make_event(type="model_call_start",
                         data={"model": model, "turn": turn},
                         turn=turn, session_id=session_id)

        stream_usage: dict = {}
        if getattr(self, '_stream_model', None):
            full_text = ""
            full_reasoning = ""
            stream_tool_calls: list[dict] = []
            try:
                async for chunk in getattr(self, '_stream_model', None)(msgs, model, tools=tools):
                    if chunk.get("type") == "chunk":
                        full_text += chunk.get("content", "")
                        reasoning = chunk.get("reasoning", "")
                        if reasoning:
                            full_reasoning += reasoning
                        yield self._make_event(type="thinking_delta",
                                         data={"content": chunk.get("content", ""),
                                               "reasoning": reasoning},
                                         turn=turn, session_id=session_id, emit=False)
                    elif chunk.get("type") == "tool_call_chunk":
                        yield self._make_event(type="tool_call_chunk",
                                         data={"name": chunk.get("name", ""),
                                               "arguments": chunk.get("arguments", ""),
                                               "id": chunk.get("id", ""),
                                               "delta": chunk.get("delta", "")},
                                         turn=turn, session_id=session_id, emit=False)
                    elif chunk.get("type") == "tool_call":
                        tc = {"id": chunk.get("id", ""), "name": chunk.get("name", ""), "params": {}}
                        try:
                            tc["params"] = json.loads(chunk.get("arguments", "{}"))
                        except Exception:
                            tc["params"] = {"raw": chunk.get("arguments", "")}
                        stream_tool_calls.append(tc)
                    elif chunk.get("type") == "usage":
                        stream_usage = {
                            "prompt_tokens": chunk.get("prompt_tokens", 0),
                            "completion_tokens": chunk.get("completion_tokens", 0),
                            "total_tokens": chunk.get("total_tokens", 0),
                        }
                    elif chunk.get("type") == "error":
                        code = chunk.get("code", 0)
                        if code == 400:
                            self._repair_messages(state)
                            if self.state_store:
                                await self.state_store.put(session_id, state)
                            state["_retry_after_repair"] = True
                            return
                        yield self._make_event(type="error",
                                         data={"code": code, "detail": chunk.get("detail", "")},
                                         turn=turn, session_id=session_id)
                        return
                else:
                    resp = {"content": full_text, "tool_calls": stream_tool_calls, "reasoning": full_reasoning}
            except Exception as exc:
                recovery = self._choose_recovery(None, str(exc).lower())
                if recovery.kind == "backoff":
                    try:
                        state, msgs, should_continue = await self._apply_recovery(
                            recovery, state, msgs, exc)
                        if should_continue:
                            await self.state_store.put(session_id, state)
                            state["_retry_call_model"] = True
                            return
                    except Exception:
                        pass
                msgs, repaired = await self._try_repair_400(exc, state, msgs, system_prompt,
                                                             session_id, model, tools)
                if repaired is not None:
                    resp = repaired
                else:
                    fallback_model = self._resolve_fallback(model, exc)
                    if fallback_model:
                        yield self._make_event(type="model_call_start",
                                         data={"model": fallback_model, "turn": turn,
                                               "fallback_from": model},
                                         turn=turn, session_id=session_id)
                        resp = await self._call_model(msgs, fallback_model, tools=tools)
                        model = fallback_model
                        state["current_model"] = fallback_model
                        stream_usage = resp.get("usage", {}) if isinstance(resp, dict) else {}
                    else:
                        raise
        else:
            try:
                resp = await self._call_model(msgs, model, tools=tools)
            except Exception as exc:
                recovery = self._choose_recovery(None, str(exc).lower())
                if recovery.kind == "backoff":
                    try:
                        state, msgs, should_continue = await self._apply_recovery(
                            recovery, state, msgs, exc)
                        if should_continue:
                            await self.state_store.put(session_id, state)
                            state["_retry_call_model"] = True
                            return
                    except Exception:
                        pass
                msgs, repaired = await self._try_repair_400(exc, state, msgs, system_prompt,
                                                             session_id, model, tools)
                if repaired is not None:
                    resp = repaired
                else:
                    fallback_model = self._resolve_fallback(model, exc)
                    if fallback_model:
                        yield self._make_event(type="model_call_start",
                                         data={"model": fallback_model, "turn": turn,
                                               "fallback_from": model},
                                         turn=turn, session_id=session_id)
                        resp = await self._call_model(msgs, fallback_model, tools=tools)
                        model = fallback_model
                        state["current_model"] = fallback_model
                    else:
                        raise
            stream_usage = resp.get("usage", {}) if isinstance(resp, dict) else {}

        if stream_usage and stream_usage.get("total_tokens", 0) > 0:
            state["last_token_usage"] = stream_usage["total_tokens"]

        # Recovery: check finish_reason
        finish_reason = resp.get("finish_reason", "stop") if isinstance(resp, dict) else "stop"
        recovery = self._choose_recovery(finish_reason, None)
        if recovery.kind in ("continue", "compact"):
            state, msgs, should_continue = await self._apply_recovery(
                recovery, state, msgs, None)
            if should_continue:
                await self.state_store.put(session_id, state)
                state["_retry_call_model"] = True
                return

        yield self._make_event(type="model_call_end",
                         data={"model": model, "turn": turn,
                               "content": resp.get("content", "") if isinstance(resp, dict) else "",
                               "usage": stream_usage},
                         turn=turn, session_id=session_id)

        if self.hook_runner:
            self.hook_runner.update_runtime(
                session_id=session_id, interaction_round=self._interaction_round)
            yield self._make_event(type="hook_start", data={"event": "post_model_call", "turn": turn},
                             turn=turn, session_id=session_id)
            h_results = await self.hook_runner.fire("post_model_call", {"response": resp})
            yield self._make_event(type="hook_end", data={"event": "post_model_call", "turn": turn,
                             "count": len(h_results), "passed": sum(1 for r in h_results if r.exit_code == 0),
                             "failed": sum(1 for r in h_results if r.exit_code != 0)},
                             turn=turn, session_id=session_id)
            self._inject_hook_messages(h_results, state)

        tool_calls = self._pars_tool_calls(resp)
        if not tool_calls:
            content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            state["messages"].append({"role": "assistant", "content": content})
            await self.state_store.put(session_id, state)
            return

        # Append assistant message with tool_calls
        response_text = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        assistant_tool_calls = [
            {"id": tc.get("id", ""), "type": "function",
             "function": {"name": tc.get("name", ""), "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)}}
            for tc in tool_calls
        ]
        assistant_msg: dict = {"role": "assistant", "content": response_text or "", "tool_calls": assistant_tool_calls}
        if isinstance(resp, dict) and resp.get("reasoning"):
            assistant_msg["reasoning_content"] = resp["reasoning"]
        state["messages"].append(assistant_msg)
        await self.state_store.put(session_id, state)
        state["_pending_tool_calls"] = tool_calls

    async def _step_execute_tools(self, state: AgentState):
        """Execute tools step — handles guard, approval, and execution."""
        session_id = state.get("session_id", "default")
        turn = state.get("current_turn", 0)
        is_streaming = getattr(self, '_stream_model', None) is not None

        tool_calls = state.pop("_pending_tool_calls", [])
        if not tool_calls:
            return

        valid_calls, denied_calls, guard_events, pending_approvals = \
            await self._step_classify_tool_calls(
                state, tool_calls, turn, session_id,
                collect_approvals=is_streaming,
            )
        for evt in guard_events:
            yield self._make_event(evt["type"], evt["data"], turn=turn, session_id=session_id)

        # Approvals — streaming yields events, invoke handles inline
        if pending_approvals:
            resolved = await self._wait_approvals(pending_approvals, session_id)
            for evt in resolved:
                yield self._make_event(evt["type"], evt["data"], turn=turn, session_id=session_id)
                if evt["data"].get("approved") is False:
                    denied_calls.append((evt["data"]["tool_name"], evt["data"]["reason"]))
                else:
                    for tc in tool_calls:
                        if tc["name"] == evt["data"]["tool_name"]:
                            valid_calls.append(tc)
                            break

        # Emit denied tool calls + inject synthetic tool results
        for tc in tool_calls:
            name = tc.get("name", "")
            matched = next((reason for dname, reason in denied_calls if dname == name), None)
            if matched is None:
                continue
            tc_id = tc.get("__tc_uid", tc.get("id", ""))
            yield self._make_event(type="tool_call_end",
                             data={"tool_name": name, "turn": turn, "id": tc_id,
                                   "success": False, "error": f"Blocked: {matched}"},
                             turn=turn, session_id=session_id)
            state["messages"].append({
                "role": "tool", "tool_call_id": tc_id,
                "content": f"[Blocked] {matched}",
            })
            logger.warning("Tool %s (%s) denied: %s", name, tc_id, matched)

        # Post-permission hook — fires after guard check, before tool execution
        # HumanLoop plugin mount point
        if self.hook_runner:
            yield self._make_event(type="hook_start", data={"event": "post_permission", "turn": turn},
                             turn=turn, session_id=session_id)
            pp_results = await self.hook_runner.fire("post_permission", {
                "tool_calls": valid_calls, "denied": denied_calls,
                "session_id": session_id, "turn": turn})
            yield self._make_event(type="hook_end", data={"event": "post_permission", "turn": turn,
                             "count": len(pp_results), "passed": sum(1 for r in pp_results if r.exit_code == 0),
                             "failed": sum(1 for r in pp_results if r.exit_code != 0)},
                             turn=turn, session_id=session_id)
            self._inject_hook_messages(pp_results, state)
        plugin_runner = getattr(self, 'plugin_runner', None)
        if plugin_runner:
            pp_ctx = PluginContext(
                session_id=session_id,
                interaction_round=self._interaction_round,
                state=state,
                messages=state.get("messages", []),
                workspace_dir=getattr(self, '_workspace_dir', '.'),
                memory_dir=getattr(self, '_memory_dir', './memory'),
                hook_data={"tool_calls": valid_calls, "denied": denied_calls,
                           "turn": turn},
            )
            await plugin_runner.fire("post_permission", pp_ctx)

        # Pre-tool-exec hooks
        if self.hook_runner:
            self.hook_runner.update_runtime(
                session_id=session_id, interaction_round=self._interaction_round)
            yield self._make_event(type="hook_start", data={"event": "pre_tool_exec", "turn": turn},
                             turn=turn, session_id=session_id)
            h_results = await self.hook_runner.fire("pre_tool_exec", {"tool_calls": valid_calls, "turn": turn})
            yield self._make_event(type="hook_end", data={"event": "pre_tool_exec", "turn": turn,
                             "count": len(h_results), "passed": sum(1 for r in h_results if r.exit_code == 0),
                             "failed": sum(1 for r in h_results if r.exit_code != 0)},
                             turn=turn, session_id=session_id)
            self._inject_hook_messages(h_results, state)

        # Emit tool_call_start
        for tc in valid_calls:
            tc_uid = self._next_tc_id(tc.get("id", ""))
            tc["__tc_uid"] = tc_uid
            yield self._make_event(type="tool_call_start",
                             data={"tool_name": tc.get("name", ""), "turn": turn,
                                   "id": tc_uid,
                                   "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)},
                             turn=turn, session_id=session_id)

        # Execute tools — with progress polling in streaming mode
        if is_streaming:
            self._tool_progress_queue = asyncio.Queue()
            tool_task = asyncio.ensure_future(
                self.tool_executor.execute(
                    valid_calls, agent_mode="",
                    engine=self, state_store=self.state_store,
                    workspace_dir=getattr(self, '_workspace_dir', ''),
                )
            )
            while not tool_task.done():
                try:
                    chunk = await asyncio.wait_for(
                        self._tool_progress_queue.get(), timeout=0.1
                    )
                    yield self._make_event("thinking_delta", chunk,
                                           turn=turn, session_id=session_id)
                except asyncio.TimeoutError:
                    continue
            results = await tool_task
        else:
            results = await self.tool_executor.execute(
                valid_calls, agent_mode="",
                engine=self, state_store=self.state_store,
                workspace_dir=getattr(self, '_workspace_dir', ''),
            )

        # Emit tool_call_end + append results
        for tc in valid_calls:
            r = results.get(tc.get("id", ""))
            tc_id = tc.get("__tc_uid", tc.get("id", ""))
            yield self._make_event(type="tool_call_end",
                             data={"tool_name": tc.get("name", ""), "turn": turn, "id": tc_id,
                                   "success": r.success if r else False,
                                   "duration_ms": r.duration_ms if r else 0,
                                   "result": str(r.data)[:500] if r and r.success and r.data else "",
                                   "error": str(r.error)[:500] if r and r.error else "",
                                   "rolled_back": r.rolled_back if r else False,
                                   "rollback_error": str(r.rollback_error)[:500] if r and r.rollback_error else ""},
                             turn=turn, session_id=session_id)
            if r:
                content = str(r.data) if r.success else f"Error: {r.error}"
                if r.rolled_back:
                    content += f" [rolled back: {r.rollback_error or 'ok'}]"
                if r.success and self.compaction and content:
                    content = await self.compaction.summarize_tool_output(
                        tc.get("name", "unknown"), content, turn
                    )
                state["messages"].append({"role": "tool", "tool_call_id": tc["id"], "content": content})

        # Rollback summary
        rb_stream = [
            {"name": r.tool_name, "rollback_error": r.rollback_error}
            for r in results.values() if r.rolled_back
        ]
        if rb_stream:
            yield self._make_event(type="rollback_executed",
                             data={"turn": turn, "rolled_back": rb_stream,
                                   "success": all(rb["rollback_error"] is None for rb in rb_stream)},
                             turn=turn, session_id=session_id)

        # Post-tool-exec hooks
        if self.hook_runner:
            self.hook_runner.update_runtime(
                session_id=session_id, interaction_round=self._interaction_round)
            yield self._make_event(type="hook_start", data={"event": "post_tool_exec", "turn": turn},
                             turn=turn, session_id=session_id)
            hr = await self.hook_runner.fire("post_tool_exec",
                {"tool_calls": valid_calls, "results": {k: {"success": v.success} for k, v in results.items()}, "turn": turn})
            yield self._make_event(type="hook_end", data={"event": "post_tool_exec", "turn": turn,
                             "count": len(hr), "passed": sum(1 for r in hr if r.exit_code == 0),
                             "failed": sum(1 for r in hr if r.exit_code != 0)},
                             turn=turn, session_id=session_id)
            self._inject_hook_messages(hr, state)

        state["tool_results"] = {
            k: {"success": v.success, "data": v.data, "error": v.error,
                "rolled_back": v.rolled_back, "rollback_error": v.rollback_error}
            for k, v in results.items()
        }
        state = self._close_tool_calls(state)

        # Sandbox persist hook — fires after tool results saved, before next round
        # UNDO plugin mount point (round-level undo with sandbox data)
        if self.hook_runner:
            self.hook_runner.update_runtime(
                session_id=session_id, interaction_round=self._interaction_round)
            await self.hook_runner.fire("sandbox_persist", {
                "session_id": session_id, "turn": turn})
        plugin_runner = getattr(self, 'plugin_runner', None)
        if plugin_runner:
            sp_ctx = PluginContext(
                session_id=session_id,
                interaction_round=self._interaction_round,
                state=state,
                messages=state.get("messages", []),
                workspace_dir=getattr(self, '_workspace_dir', '.'),
                memory_dir=getattr(self, '_memory_dir', './memory'),
                hook_data={"turn": turn},
            )
            await plugin_runner.fire("sandbox_persist", sp_ctx)

        # ErrorPolicy circuit-breaker
        executed_failures = sum(1 for v in results.values() if not v.success)
        executed_success = sum(1 for v in results.values() if v.success)
        if executed_success > 0:
            state.pop("_tool_failures", None)
        elif executed_failures > 0 and len(valid_calls) > 0:
            state.setdefault("_tool_failures", 0)
            state["_tool_failures"] += executed_failures
            if state["_tool_failures"] >= 5 and self.error_policy:
                action = self.error_policy.on_tool_error(
                    RuntimeError(f"{state['_tool_failures']} consecutive execution failures"),
                    "aggregate", state["_tool_failures"])
                if action.action == "abort":
                    yield self._make_event("error", {
                        "detail": (
                            f"Tool failure limit reached "
                            f"({state['_tool_failures']} consecutive failures). "
                            f"{action.message}")},
                        turn=turn, session_id=session_id)
                    return


        self._reset_recovery_state(state)
        await self.state_store.put(session_id, state)

    async def _execute(self, state: AgentState):
        """Single execution path — yields AgentEvent at every step.
        Used by both invoke() and astream()."""
        state = self._close_tool_calls(state)
        session_id = state.get("session_id", "default")
        self._interaction_round = state.get("interaction_round", 0) + 1
        state["interaction_round"] = self._interaction_round
        yield self._make_event(type="session_start", data={"session_id": session_id},
                         session_id=session_id)

        while self.loop_strategy.should_continue(state):
            if self._cancelled():
                yield self._make_event(type="session_end",
                                 data={"session_id": session_id, "reason": "cancelled"},
                                 session_id=session_id)
                state = self._close_tool_calls(state)
                await self.state_store.put(session_id, state)
                return

            step = self.loop_strategy.next_step(state)
            if step != "execute_tools" or not state.get("_pending_tool_calls"):
                step = "call_model"

            if step == "call_model":
                async for event in self._step_call_model(state):
                    yield event
                # Retry: 400 repair, recovery continue, or backoff — restart call_model
                if state.pop("_retry_after_repair", False) or state.pop("_retry_call_model", False):
                    state["current_turn"] = state.get("current_turn", 1) - 1
                    await self.state_store.put(session_id, state)
                    continue
                # Text-only response (no tool_calls) → done
                if not state.get("_pending_tool_calls"):
                    break
            elif step == "execute_tools":
                async for event in self._step_execute_tools(state):
                    yield event
            else:
                break

            if self.loop_strategy.should_break(state):
                break

        if self.hook_runner:
            self.hook_runner.update_runtime(
                session_id=session_id, interaction_round=self._interaction_round)
            await self.hook_runner.fire("round_end", {
                "session_id": session_id, "round": self._interaction_round})
        plugin_runner = getattr(self, 'plugin_runner', None)
        if plugin_runner:
            re_ctx = PluginContext(
                session_id=session_id,
                interaction_round=self._interaction_round,
                state=state,
                messages=state.get("messages", []),
                workspace_dir=getattr(self, '_workspace_dir', '.'),
                memory_dir=getattr(self, '_memory_dir', './memory'),
                hook_data={
                    "round": self._interaction_round,
                    "messages_count": len(state.get("messages", [])),
                    "last_token_usage": state.get("last_token_usage", 0),
                },
            )
            await plugin_runner.fire("round_end", re_ctx)
        state = self._close_tool_calls(state)
        yield self._make_event(type="session_end", data={"session_id": session_id},
                         session_id=session_id)

    async def invoke(self, state: AgentState) -> AgentState:
        """Execute agent loop — consumes all events, returns final state."""
        async for _ in self._execute(state):
            pass
        session_id = state.get("session_id", "default")
        if self.state_store:
            saved = await self.state_store.get(session_id)
            if saved:
                return saved
        return state

    async def astream(self, state: AgentState):
        """Stream execution — yields AgentEvent at each step."""
        async for event in self._execute(state):
            yield event
