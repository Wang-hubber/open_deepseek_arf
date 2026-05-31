"""GraphEngine — DI-driven Agent execution loop."""
import asyncio
import copy
import json
import logging
logger = logging.getLogger("arf.engine")
from collections import deque
from pathlib import Path
from typing import Callable
from arf.core.protocols import (
    LoopStrategy, StateStore, ToolExecutor, Planner,
    ToolResolver, MemoryStore, MemoryRetriever, MemoryWriter, HookRunner,
    GuardRunner, EventBus, ErrorPolicy, ModelRouter, CompactionStrategy,
)
from arf.core.state import AgentState, TurnContext
from arf.core.events import AgentEvent
from arf.compaction.sliding_window import DEFAULT_WINDOW_SIZE


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
        # Multi-agent support
        sub_agent_configs: dict | None = None,
        handoff_manager=None,
        memory_workspace: str = "./memory",
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
        self._call_model = call_model
        self._stream_model = stream_model
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._memory_max_tokens = memory_max_tokens
        self._memory_top_k = memory_top_k
        self._cancel_event = cancel_event
        self._interaction_round = 0
        self._memory_dir = memory_workspace
        # Round-level checkpoint manager (replaces per-agent checkpoint stacks)
        from arf.engine.round_manager import RoundManager
        self._rounds = RoundManager(max_undo_depth=max_undo_depth)
        # Multi-agent
        self._sub_agent_configs: dict = sub_agent_configs or {}
        self._handoff_manager = handoff_manager
        self._active_agent: str = ""
        self._agent_states: dict[str, AgentState] = {}

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

    def _cancelled(self) -> bool:
        """Check if execution has been cancelled (non-blocking)."""
        return self._cancel_event is not None and self._cancel_event.is_set()

    # ---- multi-agent support ----

    def _active_config(self, state: AgentState) -> dict:
        """Return (system_prompt, tools, skills, max_turns) for active agent."""
        agent_name = state.get("active_agent", "")
        if agent_name and agent_name in self._sub_agent_configs:
            sub = self._sub_agent_configs[agent_name]
            cfg = sub["config"]
            return {
                "system_prompt": sub.get("system_prompt", ""),
                "tools": cfg.tools,
                "skills": cfg.skills,
                "max_turns": cfg.effective_advanced().max_turns,
                "hooks": cfg.hooks,
                "adapters": sub.get("adapters", {}),
            }

        return {
            "system_prompt": self._system_prompt,
            "tools": [],
            "skills": [],
            "max_turns": self._max_turns,
            "hooks": [],
            "adapters": {},
        }

    async def _execute_handoff(self, state: AgentState, handoff_data: dict[str, object],
                                current_model: str) -> AgentState:
        """Execute forward handoff: save agent state → resolve → build context → swap."""
        session_id = state.get("session_id", "default")
        from_agent = (
            state.get("active_agent", "")
            or state.get("agent_name", "")
            or self._active_agent
            or ""
        )

        # 1. Save current agent state (persist for later resume)
        await self.state_store.put(
            f"{session_id}/{from_agent}" if from_agent else session_id,
            state,
        )

        # 2. Resolve target
        to_agent = await self._handoff_manager.resolve(from_agent, handoff_data)
        if not to_agent:
            state["handoff_error"] = f"No handover rule matches from '{from_agent}'"
            return state

        rule = self._handoff_manager.get_rule(from_agent, to_agent)
        if not rule:
            state["handoff_error"] = f"No rule for {from_agent} → {to_agent}"
            return state

        # 3. Record agent switch in the current round (no new checkpoint)
        self._rounds.record_handoff(from_agent or "main", to_agent)

        # Capture sub-agent's result before state swap (for return handoffs)
        sub_agent_result = ""
        for m in reversed(state.get("messages", [])):
            if m.get("role") == "assistant":
                sub_agent_result = m.get("content", "")
                break

        # 4. Try to load existing target agent state, or build fresh context
        existing_target = await self.state_store.get(f"{session_id}/{to_agent}")
        if existing_target:
            # Save sub-agent's final state before switching back (E2E Bug 3.3)
            if from_agent:
                await self.state_store.put(f"{session_id}/{from_agent}", state)
            # Resume: restore target agent's previous state
            state.update(existing_target)
            # For return handoffs: replace raw tool result with sub-agent's actual response
            if sub_agent_result and to_agent == (self._active_agent or state.get("agent_name", "")):
                msgs = state.get("messages", [])
                if msgs and msgs[-1].get("role") == "tool":
                    msgs[-1]["content"] = sub_agent_result
        else:
            # First time: build target context from handoff data
            target_cfg = self._sub_agent_configs.get(to_agent, {})
            target_prompt = target_cfg.get("system_prompt", "")
            new_messages = self._handoff_manager.build_target_context(
                from_state=state,
                rule=rule,
                handoff_data=handoff_data,
                target_system_prompt=target_prompt,
            )

            # Generate task summary (if configured)
            if rule.context.task_summary and self._handoff_manager._system_model_call:
                try:
                    summary = await self._handoff_manager._system_model_call(
                        f"Summarize this handoff task in one sentence (Chinese):\n"
                        f"Task: {handoff_data.get('task', '')}\n"
                        f"Context: {handoff_data.get('context', '')}"
                    )
                    for i, m in enumerate(new_messages):
                        if m.get("content") == "__TASK_SUMMARY_PLACEHOLDER__":
                            new_messages[i] = {
                                "role": "system",
                                "content": f"[Task Summary] {summary.strip()}",
                            }
                except Exception:
                    new_messages = [m for m in new_messages
                                    if m.get("content") != "__TASK_SUMMARY_PLACEHOLDER__"]
            else:
                new_messages = [m for m in new_messages
                                if m.get("content") != "__TASK_SUMMARY_PLACEHOLDER__"]

            state["messages"] = new_messages
            state["current_turn"] = 0
            state["tool_results"] = {}

        # 5. Swap active agent
        state["active_agent"] = to_agent
        state["handoff_task"] = handoff_data.get("task", "")

        # 6. Emit agent_switch
        self._emit("agent_switch", {
            "from": from_agent,
            "to": to_agent,
            "task": handoff_data.get("task", ""),
        }, session_id=session_id, agent_name=to_agent)

        logging.getLogger("arf.engine").info(
            "Handoff: %s → %s, task: %.80s", from_agent, to_agent,
            handoff_data.get("task", "")
        )
        return state

    async def _restore_from_handoff(self, state: AgentState,
                                      handoff_data: dict[str, object]) -> AgentState:
        """Restore original agent after sub-agent handoff back."""
        session_id = state.get("session_id", "default")
        current_agent = state.get("active_agent", "")
        target_agent = self._active_agent or state.get("agent_name", "main")

        # Get sub-agent's last assistant message as result
        result_content = ""
        for m in reversed(state.get("messages", [])):
            if m.get("role") == "assistant":
                result_content = m.get("content", "")
                break

        if not result_content:
            result_content = "(handoff completed, no response)"

        # Save sub-agent's final state
        if current_agent:
            await self.state_store.put(f"{session_id}/{current_agent}", state)

        # Load main agent's state from store (saved by _execute_handoff)
        from_state = await self.state_store.get(f"{session_id}/{target_agent}")
        if from_state:
            messages = from_state.get("messages", [])
            # Replace the original handoff tool result with sub-agent's response
            if messages and messages[-1].get("role") == "tool":
                messages[-1]["content"] = result_content
        else:
            messages = state.get("messages", [])

        # Record the return switch in the current round
        self._rounds.record_handoff(current_agent, target_agent)

        # Emit agent_switch back
        self._emit("agent_switch", {
            "from": current_agent,
            "to": target_agent,
            "task": "handoff complete",
        }, session_id=session_id, agent_name=target_agent)

        # Swap state back to original agent
        state["messages"] = messages
        state["active_agent"] = target_agent
        state["tool_results"] = {}
        state.pop("handoff_task", None)
        state = self._close_tool_calls(state)

        return state

    async def _resolve_tools_for_agent(self, state: AgentState, active: dict[str, object]) -> list[dict[str, object]]:
        """Get tool definitions for the active agent, falling back to resolver."""
        active_tools = active.get("tools", [])
        if active_tools:
            result = []
            for t in active_tools:
                if hasattr(t, "name"):
                    result.append({
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    })
                elif isinstance(t, dict):
                    result.append(t)
            return result
        if self.tool_resolver:
            return await self.tool_resolver.get_tool_definitions(
                self._last_user_message(state), top_k=10
            )
        return []

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

        if not self.guard_runner:
            return tool_calls, [], [], []

        for tc in tool_calls:
            name = tc.get("name", "")
            params = tc.get("params", {})

            if pipeline_data:
                from arf.skills.pipeline import SkillPipeline
                sp = SkillPipeline(pipeline_data.get("steps", []))
                if not sp.can_execute(name, completed):
                    reason = sp.validation_error(name, completed)
                    denied_calls.append((name, reason))
                    events.append({"type": "guard_block",
                                   "data": {"tool_name": name, "guard": "pipeline", "reason": reason}})
                    continue

            gr = await self.guard_runner.check_tool_params(name, params)
            if not gr.allowed:
                denied_calls.append((name, gr.reason))
                events.append({"type": "guard_block",
                               "data": {"tool_name": name, "guard": "path_check", "reason": gr.reason}})
                continue

            perm = self.guard_runner.check_tool_permission(name, params)
            if perm == "deny":
                denied_calls.append((name, "denied by permission config"))
                events.append({"type": "guard_block",
                               "data": {"tool_name": name, "guard": "permission",
                                        "reason": "denied by config"}})
                continue

            if perm == "ask":
                needs_approval = self.approval_enabled and (
                    not self._approval_allowlist or name in self._approval_allowlist
                )
                if needs_approval:
                    decision_id = f"{session_id}_{name}_{id(tc)}"
                    if collect_approvals:
                        events.append({"type": "approval_required",
                                       "data": {"decision_id": decision_id, "tool_name": name,
                                                "params": params}})
                        pending_approvals.append({
                            "decision_id": decision_id, "tool_name": name, "params": params,
                        })
                        continue  # caller will wait_approvals then re-classify
                    else:
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
                    denied_calls.append((name, "requires approval (channel not enabled)"))
                    events.append({"type": "guard_block",
                                   "data": {"tool_name": name, "guard": "approval",
                                            "reason": "requires approval (channel not enabled)"}})
                    continue

            events.append({"type": "guard_pass", "data": {"tool_name": name}})
            completed.add(name)
            valid_calls.append(tc)

        if pipeline_data:
            state["active_pipeline"]["completed"] = list(completed)

        return valid_calls, denied_calls, events, pending_approvals

    async def _wait_approvals(
        self, pending: list[dict], session_id: str,
    ) -> list[dict]:
        """Wait for each pending approval and return resolved events."""
        events: list[dict] = []
        for pa in pending:
            decision_id = pa["decision_id"]
            tool_name = pa["tool_name"]
            approval_evt = asyncio.Event()
            self._pending_approvals[decision_id] = approval_evt
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

    async def invoke(self, state: AgentState) -> AgentState:
        state = self._close_tool_calls(state)
        session_id = state.get("session_id", "default")
        self._interaction_round = state.get("interaction_round", 0) + 1
        state["interaction_round"] = self._interaction_round
        self._emit("session_start", {"session_id": session_id}, session_id=session_id)

        while self.loop_strategy.should_continue(state):
            if self._cancelled():
                self._emit("session_end", {"session_id": session_id, "reason": "cancelled"}, session_id=session_id)
                state = self._close_tool_calls(state)
                await self.state_store.put(session_id, state)
                break

            step = self.loop_strategy.next_step(state)

            # Normalize: unknown/mock steps default to call_model
            if step != "execute_tools" or not state.get("_pending_tool_calls"):
                step = "call_model"

            # ---- call_model ----
            if step == "call_model":
                turn = state.get("current_turn", 0) + 1
                state["current_turn"] = turn

                user_msg = self._last_user_message(state)
                self._emit("user_input", {"content": user_msg, "turn": turn}, session_id=session_id)

                # Route to best model for this turn
                model = state["current_model"]
                if self.model_router:
                    routed = await self.model_router.route(self._last_user_message(state), state.get("messages", []))
                    model = routed or model
                    state["current_model"] = model

                # Compaction — after routing, before model call
                if self.compaction:
                    cd = state.get("_compaction_cooldown", 0)
                    if cd > 0:
                        state["_compaction_cooldown"] = cd - 1
                    window = self._model_windows.get(model, DEFAULT_WINDOW_SIZE) if hasattr(self, '_model_windows') else DEFAULT_WINDOW_SIZE
                    if self.compaction.should_compact(state, window_size=window):
                        self._emit("compaction_start", {"turn": turn, "model": model, "msg_count": len(state.get("messages", []))}, session_id=session_id)
                        state = await self.compaction.compact(state)
                        state = self._repair_messages(state)
                        self._emit("compaction_end", {"turn": turn, "msg_count": len(state.get("messages", [])), "summary_len": len(state.get("context_summary", ""))}, session_id=session_id)

                # Get tool definitions — use active agent's tools
                active = self._active_config(state)
                self.loop_strategy.max_turns = active["max_turns"]
                tools = await self._resolve_tools_for_agent(state, active)

                # Build messages & call model
                system_prompt = active["system_prompt"]
                summary = state.get("context_summary", "")
                if summary:
                    if "{{MEMORY}}" in system_prompt:
                        system_prompt = system_prompt.replace("{{MEMORY}}", f"## Memory\n{summary}")
                    else:
                        system_prompt += f"\n\n## Memory\n{summary}"
                # Proactive repair before every model call (E2E Bug 3.1)
                state = self._repair_messages(state)
                msgs = [{"role": "system", "content": system_prompt}]
                msgs.extend(state.get("messages", []))

                if self.hook_runner:
                    self.hook_runner.update_runtime(
                        session_id=session_id,
                        interaction_round=self._interaction_round,
                    )
                    self._emit("hook_start", {"event": "pre_model_call", "turn": turn}, session_id=session_id)
                    results = await self.hook_runner.fire("pre_model_call", {"messages": msgs})
                    self._emit("hook_end", {"event": "pre_model_call", "turn": turn,
                               "count": len(results),
                               "passed": sum(1 for r in results if r.exit_code == 0),
                               "failed": sum(1 for r in results if r.exit_code != 0)},
                               session_id=session_id)
                    self._inject_hook_messages(results, state)

                if not self._call_model:
                    break

                self._emit("model_call_start", {"model": model, "turn": turn}, session_id=session_id)
                response = None
                try:
                    # Debug: log last 10 messages before model call
                    _dbg = []
                    for _m in msgs[-10:]:
                        _dbg.append({"role": _m.get("role",""), "tc_ids": [tc.get("id","") for tc in _m.get("tool_calls",[])], "content_len": len(str(_m.get("content","")))})
                    logger = logging.getLogger("arf.engine")
                    logger.info("PRE_CALL msgs tail: %s", json.dumps(_dbg, ensure_ascii=False))
                    response = await self._call_model(msgs, model, tools=tools)
                except Exception as exc:
                    msgs, response = await self._try_repair_400(exc, state, msgs, system_prompt,
                                                                 session_id, model, tools)
                    if response is None:
                        fallback_model = self._resolve_fallback(model, exc)
                        if fallback_model:
                            self._emit("model_call_start", {"model": fallback_model, "turn": turn,
                                       "fallback_from": model}, session_id=session_id)
                            response = await self._call_model(msgs, fallback_model, tools=tools)
                            model = fallback_model
                            state["current_model"] = fallback_model
                        else:
                            raise
                if isinstance(response, dict) and response.get("usage"):
                    state["last_token_usage"] = response["usage"].get("total_tokens", 0)
                self._emit("model_call_end", {"model": model, "turn": turn,
                           "usage": response.get("usage", {}) if isinstance(response, dict) else {},
                           "content": response.get("content", "") if isinstance(response, dict) else ""},
                           session_id=session_id)

                if self.hook_runner:
                    self.hook_runner.update_runtime(
                        session_id=session_id,
                        interaction_round=self._interaction_round,
                    )
                    self._emit("hook_start", {"event": "post_model_call", "turn": turn}, session_id=session_id)
                    results = await self.hook_runner.fire("post_model_call", {"response": response})
                    self._emit("hook_end", {"event": "post_model_call", "turn": turn,
                               "count": len(results),
                               "passed": sum(1 for r in results if r.exit_code == 0),
                               "failed": sum(1 for r in results if r.exit_code != 0)},
                               session_id=session_id)
                    self._inject_hook_messages(results, state)

                # Guard output
                response_text = response if isinstance(response, str) else response.get("content", "")
                if self.guard_runner and response_text:
                    gr = await self.guard_runner.check_output(response_text, {})
                    if not gr.allowed:
                        if self.error_policy:
                            ctx = TurnContext(session_id=session_id, agent_name=state.get("agent_name", ""),
                                              turn=turn, current_model=state.get("current_model", ""),
                                              available_models=[], last_user_message=self._last_user_message(state))
                            action = self.error_policy.on_guardrail_block(gr, ctx)
                            if action.action == "abort":
                                break
                    elif gr.modified_message:
                        response_text = gr.modified_message

                # Parse tool calls
                tool_calls = self._pars_tool_calls(response)
                if not tool_calls:
                    state["messages"].append({"role": "assistant", "content": response_text})
                    await self.state_store.put(session_id, state)
                    break

                # Append assistant message with tool_calls
                assistant_tool_calls = [
                    {"id": tc.get("id", ""), "type": "function",
                     "function": {"name": tc.get("name", ""), "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)}}
                    for tc in tool_calls
                ]
                assistant_msg = {"role": "assistant", "content": response_text, "tool_calls": assistant_tool_calls}
                if isinstance(response, dict) and response.get("reasoning"):
                    assistant_msg["reasoning_content"] = response["reasoning"]
                state["messages"].append(assistant_msg)
                await self.state_store.put(session_id, state)

                # Carry parsed tool_calls to next phase
                state["_pending_tool_calls"] = tool_calls

            # ---- execute_tools ----
            elif step == "execute_tools":
                tool_calls = state.pop("_pending_tool_calls", [])
                turn = state.get("current_turn", 0)
                if not tool_calls:
                    continue

                # Guard tool params + pipeline + permissions
                valid_calls, denied_calls, guard_events, _ = await self._step_classify_tool_calls(
                    state, tool_calls, turn, session_id,
                )
                for evt in guard_events:
                    self._emit(evt["type"], evt["data"], session_id=session_id)

                # Emit denied tool calls and inject synthetic tool results
                for tc in tool_calls:
                    name = tc.get("name", "")
                    matched = next((reason for dname, reason in denied_calls if dname == name), None)
                    if matched is None:
                        continue
                    tc_id = tc.get("id", "")
                    self._emit("tool_call_end", {"tool_name": name, "turn": turn, "id": tc_id,
                               "success": False, "error": f"Blocked: {matched}"},
                               session_id=session_id)
                    state["messages"].append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": f"[Blocked] {matched}",
                    })
                    logger.warning("Tool %s (%s) denied: %s", name, tc_id, matched)

                # Hooks + execute
                if self.hook_runner:
                    self.hook_runner.update_runtime(
                        session_id=session_id,
                        interaction_round=self._interaction_round,
                    )
                    self._emit("hook_start", {"event": "pre_tool_exec", "turn": turn}, session_id=session_id)
                    results = await self.hook_runner.fire("pre_tool_exec", {"tool_calls": valid_calls, "turn": turn})
                    self._emit("hook_end", {"event": "pre_tool_exec", "turn": turn,
                               "count": len(results), "passed": sum(1 for r in results if r.exit_code == 0),
                               "failed": sum(1 for r in results if r.exit_code != 0)}, session_id=session_id)
                    self._inject_hook_messages(results, state)
                for tc in valid_calls:
                    self._emit("tool_call_start", {"tool_name": tc.get("name", ""), "turn": turn,
                               "id": tc.get("id", ""),
                               "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)},
                               session_id=session_id)
                agent_mode = state.get("active_agent", "")
                results = await self.tool_executor.execute(
                valid_calls, agent_mode=agent_mode,
                engine=self, state_store=self.state_store,
            )
                for tc in valid_calls:
                    r = results.get(tc.get("id", ""))
                    self._emit("tool_call_end", {"tool_name": tc.get("name", ""), "turn": turn, "id": tc.get("id", ""),
                              "success": r.success if r else False, "duration_ms": r.duration_ms if r else 0,
                              "result": str(r.data)[:500] if r and r.success and r.data else "",
                              "error": str(r.error)[:500] if r and r.error else "",
                              "rolled_back": r.rolled_back if r else False,
                              "rollback_error": str(r.rollback_error)[:500] if r and r.rollback_error else ""},
                              session_id=session_id)
                # Emit consolidated rollback event
                rolled_back = [
                    {"name": r.tool_name, "rollback_error": r.rollback_error}
                    for r in results.values() if r.rolled_back
                ]
                if rolled_back:
                    self._emit("rollback_executed", {
                        "turn": turn,
                        "rolled_back": rolled_back,
                        "success": all(rb["rollback_error"] is None for rb in rolled_back),
                    }, session_id=session_id)

                if self.hook_runner:
                    self.hook_runner.update_runtime(
                        session_id=session_id,
                        interaction_round=self._interaction_round,
                    )
                    self._emit("hook_start", {"event": "post_tool_exec", "turn": turn}, session_id=session_id)
                    hook_results = await self.hook_runner.fire("post_tool_exec", {"tool_calls": valid_calls, "results": {k: {"success": v.success} for k, v in results.items()}, "turn": turn})
                    self._emit("hook_end", {"event": "post_tool_exec", "turn": turn,
                               "count": len(hook_results), "passed": sum(1 for r in hook_results if r.exit_code == 0),
                               "failed": sum(1 for r in hook_results if r.exit_code != 0)}, session_id=session_id)
                    self._inject_hook_messages(hook_results, state)

                # Add results to messages (with tool output summarization)
                for tc in valid_calls:
                    r = results.get(tc.get("id", ""))
                    if r:
                        content = str(r.data) if r.success else f"Error: {r.error}"
                        if r.rolled_back:
                            content += f" [rolled back: {r.rollback_error or 'ok'}]"
                        if r.success and self.compaction and content:
                            content = await self.compaction.summarize_tool_output(
                                tc.get("name", "unknown"), content, turn
                            )
                        state["messages"].append({
                            "role": "tool", "tool_call_id": tc["id"],
                            "content": content,
                        })
                state["tool_results"] = {
                    k: {"success": v.success, "data": v.data, "error": v.error,
                        "rolled_back": v.rolled_back, "rollback_error": v.rollback_error}
                    for k, v in results.items()
                }
                # Ensure message sequence is valid before next_step re-evaluates
                state = self._close_tool_calls(state)

                # Handoff detection (invoke)
                if self._handoff_manager and self._handoff_manager.has_rules:
                    handoff_signal = self._handoff_manager.detect(state["tool_results"])
                    if handoff_signal:
                        state = await self._execute_handoff(state, handoff_signal, state["current_model"])
                        if state.get("handoff_error"):
                            msgs = state["messages"]
                            if msgs and msgs[-1].get("role") == "tool":
                                msgs[-1]["content"] = f"Handoff failed: {state['handoff_error']}"
                            del state["handoff_error"]
                            await self.state_store.put(session_id, state)
                        else:
                            self.loop_strategy.max_turns = self._active_config(state)["max_turns"]
                        continue

                # Checkpoint
                await self.state_store.put(session_id, state)

                if self.loop_strategy.should_break(state):
                    break


        if self.hook_runner:
            self.hook_runner.update_runtime(
                session_id=session_id,
                interaction_round=self._interaction_round,
            )
            await self.hook_runner.fire("round_end", {
                "session_id": session_id,
                "round": self._interaction_round,
            })
        state = self._close_tool_calls(state)
        self._emit("session_end", {"session_id": session_id}, session_id=session_id)
        return state

    async def astream(self, state: AgentState):
        """Streaming execution — yields AgentEvent at each step of the loop.
        Dispatch driven by loop_strategy.next_step() so control modes are
        pluggable without changing the engine."""
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
                break

            step = self.loop_strategy.next_step(state)

            # Normalize: unknown/mock steps default to call_model
            if step != "execute_tools" or not state.get("_pending_tool_calls"):
                step = "call_model"

            # ---- call_model ----
            if step == "call_model":
                turn = state.get("current_turn", 0) + 1
                state["current_turn"] = turn

                user_msg = self._last_user_message(state)
                yield self._make_event(type="user_input",
                                 data={"content": user_msg, "turn": turn},
                                 turn=turn, session_id=session_id)

                if not self._call_model:
                    break

                # Route to best model for this turn
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
                        yield self._make_event(type="compaction_end",
                                         data={"turn": turn, "msg_count": len(state.get("messages", [])),
                                               "summary_len": len(state.get("context_summary", ""))},
                                         turn=turn, session_id=session_id)

                # Get tool definitions
                tools: list[dict] = []
                if self.tool_resolver:
                    active = self._active_config(state)
                    self.loop_strategy.max_turns = active["max_turns"]
                    tools = await self._resolve_tools_for_agent(state, active)

                system_prompt = active["system_prompt"]
                summary = state.get("context_summary", "")
                if summary:
                    if "{{MEMORY}}" in system_prompt:
                        system_prompt = system_prompt.replace("{{MEMORY}}", f"## Memory\n{summary}")
                    else:
                        system_prompt += f"\n\n## Memory\n{summary}"
                # Proactive repair before every model call (E2E Bug 3.1)
                state = self._repair_messages(state)
                msgs = [{"role": "system", "content": system_prompt}]
                msgs.extend(state.get("messages", []))

                if self.hook_runner:
                    self.hook_runner.update_runtime(
                        session_id=session_id,
                        interaction_round=self._interaction_round,
                    )
                    yield self._make_event(type="hook_start", data={"event": "pre_model_call", "turn": turn},
                                     turn=turn, session_id=session_id)
                    h_results = await self.hook_runner.fire("pre_model_call", {"messages": msgs})
                    yield self._make_event(type="hook_end", data={"event": "pre_model_call", "turn": turn,
                                     "count": len(h_results), "passed": sum(1 for r in h_results if r.exit_code == 0),
                                     "failed": sum(1 for r in h_results if r.exit_code != 0)},
                                     turn=turn, session_id=session_id)
                    self._inject_hook_messages(h_results, state)

                yield self._make_event(type="model_call_start",
                                 data={"model": model, "turn": turn},
                                 turn=turn, session_id=session_id)

                if self._stream_model:
                    full_text = ""
                    full_reasoning = ""
                    stream_usage: dict = {}
                    stream_tool_calls: list[dict] = []
                    try:
                        async for chunk in self._stream_model(msgs, model, tools=tools):
                            if chunk.get("type") == "chunk":
                                full_text += chunk.get("content", "")
                                reasoning = chunk.get("reasoning", "")
                                if reasoning:
                                    full_reasoning += reasoning
                                yield self._make_event(type="thinking_delta",
                                                 data={"content": chunk.get("content", ""),
                                                       "reasoning": reasoning},
                                                 turn=turn, session_id=session_id,
                                                 emit=False)
                            elif chunk.get("type") == "tool_call_chunk":
                                yield self._make_event(type="tool_call_chunk",
                                                 data={"name": chunk.get("name", ""),
                                                       "arguments": chunk.get("arguments", ""),
                                                       "id": chunk.get("id", ""),
                                                       "delta": chunk.get("delta", "")},
                                                 turn=turn, session_id=session_id,
                                                 emit=False)
                            elif chunk.get("type") == "tool_call":
                                tc = {"id": chunk.get("id", ""), "name": chunk.get("name", ""),
                                      "params": {}}
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
                                    resp = None
                                    break
                                yield self._make_event(type="error",
                                                 data={"code": code,
                                                       "detail": chunk.get("detail", "")},
                                                 turn=turn, session_id=session_id)
                                resp = {"content": "", "tool_calls": []}
                                break
                        else:
                            resp = {"content": full_text, "tool_calls": stream_tool_calls, "reasoning": full_reasoning}
                    except Exception as exc:
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
                else:
                    try:
                        resp = await self._call_model(msgs, model, tools=tools)
                    except Exception as exc:
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

                # After stream: check if we need to retry after 400 repair
                if state.pop("_retry_after_repair", False):
                    state["current_turn"] = state.get("current_turn", 1) - 1
                    await self.state_store.put(session_id, state)
                    continue  # restart while loop → call_model step with repaired state

                yield self._make_event(type="model_call_end",
                                 data={"model": model, "turn": turn,
                                       "content": resp.get("content", ""),
                                       "usage": stream_usage},
                                 turn=turn, session_id=session_id)

                if self.hook_runner:
                    self.hook_runner.update_runtime(
                        session_id=session_id,
                        interaction_round=self._interaction_round,
                    )
                    yield self._make_event(type="hook_start", data={"event": "post_model_call", "turn": turn},
                                     turn=turn, session_id=session_id)
                    h_results = await self.hook_runner.fire("post_model_call", {"response": resp})
                    yield self._make_event(type="hook_end", data={"event": "post_model_call", "turn": turn,
                                     "count": len(h_results), "passed": sum(1 for r in h_results if r.exit_code == 0),
                                     "failed": sum(1 for r in h_results if r.exit_code != 0)},
                                     turn=turn, session_id=session_id)
                    self._inject_hook_messages(h_results, state)

                if stream_usage and stream_usage.get("total_tokens", 0) > 0:
                    state["last_token_usage"] = stream_usage["total_tokens"]

                tool_calls = self._pars_tool_calls(resp)
                if not tool_calls:
                    state["messages"].append({"role": "assistant", "content": resp.get("content", "")})
                    await self.state_store.put(session_id, state)
                    break

                # Append assistant message with tool_calls
                assistant_tool_calls = [
                    {"id": tc.get("id", ""), "type": "function",
                     "function": {"name": tc.get("name", ""), "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)}}
                    for tc in tool_calls
                ]
                assistant_msg: dict = {"role": "assistant", "content": resp.get("content") or "", "tool_calls": assistant_tool_calls}
                if resp.get("reasoning"):
                    assistant_msg["reasoning_content"] = resp["reasoning"]
                state["messages"].append(assistant_msg)
                await self.state_store.put(session_id, state)

                state["_pending_tool_calls"] = tool_calls

            # ---- execute_tools ----
            elif step == "execute_tools":
                tool_calls = state.pop("_pending_tool_calls", [])
                turn = state.get("current_turn", 0)
                if not tool_calls:
                    continue

                valid_calls, denied_calls, guard_events, pending_approvals = \
                    await self._step_classify_tool_calls(
                        state, tool_calls, turn, session_id, collect_approvals=True,
                    )
                for evt in guard_events:
                    yield self._make_event(evt["type"], evt["data"], turn=turn, session_id=session_id)

                # Wait for approvals — must happen AFTER yielding events so SSE gets them
                if pending_approvals:
                    resolved = await self._wait_approvals(pending_approvals, session_id)
                    for evt in resolved:
                        yield self._make_event(evt["type"], evt["data"], turn=turn, session_id=session_id)
                        # Denied approvals become denied_calls
                        if evt["data"].get("approved") is False:
                            denied_calls.append((evt["data"]["tool_name"], evt["data"]["reason"]))
                        else:
                            # Approved — add to valid_calls
                            for tc in tool_calls:
                                if tc["name"] == evt["data"]["tool_name"]:
                                    valid_calls.append(tc)
                                    break

                for tc in tool_calls:
                    name = tc.get("name", "")
                    matched = next((reason for dname, reason in denied_calls if dname == name), None)
                    if matched is None:
                        continue
                    tc_id = tc.get("id", "")
                    yield self._make_event(type="tool_call_end",
                                     data={"tool_name": name, "turn": turn, "id": tc_id,
                                           "success": False, "error": f"Blocked: {matched}"},
                                     turn=turn, session_id=session_id)
                    state["messages"].append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": f"[Blocked] {matched}",
                    })
                    logger.warning("Tool %s (%s) denied: %s", name, tc_id, matched)

                if self.hook_runner:
                    self.hook_runner.update_runtime(
                        session_id=session_id,
                        interaction_round=self._interaction_round,
                    )
                    yield self._make_event(type="hook_start", data={"event": "pre_tool_exec", "turn": turn},
                                     turn=turn, session_id=session_id)
                    h_results = await self.hook_runner.fire("pre_tool_exec", {"tool_calls": valid_calls, "turn": turn})
                    yield self._make_event(type="hook_end", data={"event": "pre_tool_exec", "turn": turn,
                                     "count": len(h_results), "passed": sum(1 for r in h_results if r.exit_code == 0),
                                     "failed": sum(1 for r in h_results if r.exit_code != 0)},
                                     turn=turn, session_id=session_id)
                    self._inject_hook_messages(h_results, state)
                for tc in valid_calls:
                    yield self._make_event(type="tool_call_start",
                                     data={"tool_name": tc.get("name", ""), "turn": turn,
                                           "id": tc.get("id", ""),
                                           "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)},
                                     turn=turn, session_id=session_id)
                agent_mode = state.get("active_agent", "")
                results = await self.tool_executor.execute(
                valid_calls, agent_mode=agent_mode,
                engine=self, state_store=self.state_store,
            )
                for tc in valid_calls:
                    r = results.get(tc.get("id", ""))
                    yield self._make_event(type="tool_call_end",
                                     data={"tool_name": tc.get("name", ""), "turn": turn, "id": tc.get("id", ""),
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
                        state["messages"].append({"role": "tool", "tool_call_id": tc["id"],
                                                  "content": content})
                rb_stream = [
                    {"name": r.tool_name, "rollback_error": r.rollback_error}
                    for r in results.values() if r.rolled_back
                ]
                if rb_stream:
                    yield self._make_event(type="rollback_executed",
                                     data={"turn": turn, "rolled_back": rb_stream,
                                           "success": all(rb["rollback_error"] is None for rb in rb_stream)},
                                     turn=turn, session_id=session_id)
                if self.hook_runner:
                    self.hook_runner.update_runtime(
                        session_id=session_id,
                        interaction_round=self._interaction_round,
                    )
                    yield self._make_event(type="hook_start", data={"event": "post_tool_exec", "turn": turn},
                                     turn=turn, session_id=session_id)
                    hr = await self.hook_runner.fire("post_tool_exec", {"tool_calls": valid_calls, "results": {k: {"success": v.success} for k, v in results.items()}, "turn": turn})
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

                if self._handoff_manager and self._handoff_manager.has_rules:
                    handoff_signal = self._handoff_manager.detect(state["tool_results"])
                    if handoff_signal:
                        state = await self._execute_handoff(state, handoff_signal, state["current_model"])
                        if state.get("handoff_error"):
                            msgs = state["messages"]
                            if msgs and msgs[-1].get("role") == "tool":
                                msgs[-1]["content"] = f"Handoff failed: {state['handoff_error']}"
                            del state["handoff_error"]
                            yield self._make_event(type="error",
                                             data={"detail": f"Handoff failed: {state.get('handoff_error', '')}"},
                                             turn=turn, session_id=session_id)
                        else:
                            self.loop_strategy.max_turns = self._active_config(state)["max_turns"]
                        await self.state_store.put(session_id, state)
                        continue

                await self.state_store.put(session_id, state)

                if self.loop_strategy.should_break(state):
                    break


        if self.hook_runner:
            self.hook_runner.update_runtime(
                session_id=session_id,
                interaction_round=self._interaction_round,
            )
            await self.hook_runner.fire("round_end", {
                "session_id": session_id,
                "round": self._interaction_round,
            })
        state = self._close_tool_calls(state)
        yield self._make_event(type="session_end", data={"session_id": session_id},
                         session_id=session_id)
