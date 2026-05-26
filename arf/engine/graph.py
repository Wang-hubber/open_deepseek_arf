"""GraphEngine — DI-driven Agent execution loop."""
import asyncio
import copy
import json
import logging
from collections import deque
from pathlib import Path
from typing import Callable
from arf.core.protocols import (
    LoopStrategy, StateStore, ToolExecutor, TransactionContext, Planner,
    ToolResolver, MemoryStore, MemoryRetriever, MemoryWriter, HookRunner,
    GuardRunner, EventBus, ErrorPolicy, ModelRouter, CompactionStrategy,
)
from arf.core.state import AgentState, TurnContext
from arf.core.events import AgentEvent


class GraphEngine:
    def __init__(
        self,
        *,
        loop_strategy: LoopStrategy,
        state_store: StateStore,
        tool_executor: ToolExecutor,
        tool_resolver: ToolResolver,
        transaction_ctx: TransactionContext | None = None,
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
        approval_enabled: bool = False,
        approval_allowlist: list[str] | None = None,
        # Multi-agent support
        sub_agent_configs: dict | None = None,
        handoff_manager=None,
    ):
        self.loop_strategy = loop_strategy
        self.approval_enabled = approval_enabled
        self._approval_allowlist: set[str] = set(approval_allowlist or [])
        self._pending_approvals: dict[str, asyncio.Event] = {}  # decision_id → set on approve
        self._approval_results: dict[str, bool] = {}  # decision_id → True/False
        self.state_store = state_store
        self.tool_executor = tool_executor
        self.tool_resolver = tool_resolver
        self.transaction_ctx = transaction_ctx
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
        self._cancel_event = cancel_event
        self._checkpoints: deque[dict] = deque(maxlen=3)  # rolling 3 snapshots
        self._interaction_round = 0
        # Multi-agent
        self._sub_agent_configs: dict = sub_agent_configs or {}
        self._handoff_manager = handoff_manager
        self._handoff_checkpoint: dict | None = None
        self._active_agent: str = ""
        # Per-agent checkpoint stacks (keyed by agent_name)
        self._agent_checkpoints: dict[str, deque] = {}
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

    def push_checkpoint(self, state: AgentState, workspace_dir: str = "") -> None:
        """Save a deep copy of state and snapshot workspace files (max 5).

        Files are backed up to memory/checkpoints/{round}/.
        On undo, files are restored from the matching checkpoint.
        """
        import shutil
        round_num = state.get("interaction_round", 0)
        snapshot = copy.deepcopy(dict(state))
        self._checkpoints.append(snapshot)

        # Snapshot workspace files
        wsp = Path(workspace_dir) if workspace_dir else Path("workspaces/default")
        ckpt_dir = Path("memory/checkpoints") / str(round_num)
        if ckpt_dir.exists():
            shutil.rmtree(ckpt_dir)
        ckpt_dir.mkdir(parents=True, exist_ok=True)

        # Backup files modified in workspace (non-dir, non-git)
        if wsp.exists():
            for f in wsp.rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    rel = f.relative_to(wsp)
                    dest = ckpt_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)

    def undo(self, steps: int = 1, workspace_dir: str = "") -> AgentState | None:
        """Pop N checkpoints, restore state and workspace files, or None.

        The POPPED checkpoint represents the state BEFORE the last N rounds,
        so we restore from it (not from the remaining stack top).
        """
        import shutil
        if steps < 1 or steps > len(self._checkpoints):
            return None

        # Pop N checkpoints — keep reference to the last popped (target to restore)
        target = None
        for _ in range(steps):
            target = self._checkpoints.pop()

        if target is None:
            return None

        target_round = target.get("interaction_round", 0)

        # Restore workspace files from target checkpoint
        wsp = Path(workspace_dir) if workspace_dir else Path("workspaces/default")
        ckpt_dir = Path("memory/checkpoints") / str(target_round)

        if ckpt_dir.exists() and wsp.exists():
            # Remove current workspace files
            for f in wsp.rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    f.unlink()
            # Restore from checkpoint
            for f in ckpt_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(ckpt_dir)
                    dest = wsp / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
            # Clean up restored checkpoint dir + all newer ones
            for d in Path("memory/checkpoints").iterdir():
                if d.is_dir() and int(d.name) >= target_round:
                    shutil.rmtree(d)

        return copy.deepcopy(target)

    def checkpoint_count(self) -> int:
        return len(self._checkpoints)

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

    async def _execute_handoff(self, state: AgentState, handoff_data: dict,
                                current_model: str) -> AgentState:
        """Execute forward handoff: save agent state → resolve → build context → swap."""
        session_id = state.get("session_id", "default")
        from_agent = state.get("active_agent", "") or self._active_agent or ""

        # 1. Save current agent's state + checkpoints
        self._agent_states[from_agent or "main"] = dict(state)
        self._agent_checkpoints[from_agent or "main"] = self._checkpoints
        await self.state_store.put(
            f"{session_id}/{from_agent}" if from_agent else session_id,
            state,
        )

        # Save handoff checkpoint for later restoration
        self._handoff_checkpoint = {
            "messages": list(state.get("messages", [])),
            "active_agent": from_agent,
            "current_turn": state.get("current_turn", 0),
            "interaction_round": state.get("interaction_round", 0),
        }

        # 2. Resolve target
        to_agent = await self._handoff_manager.resolve(from_agent, handoff_data)
        if not to_agent:
            state["handoff_error"] = f"No handover rule matches from '{from_agent}'"
            return state

        rule = self._handoff_manager.get_rule(from_agent, to_agent)
        if not rule:
            state["handoff_error"] = f"No rule for {from_agent} → {to_agent}"
            return state

        # 3. Try to load existing target agent state, or build fresh context
        existing_target = await self.state_store.get(f"{session_id}/{to_agent}")
        if existing_target:
            # Resume: restore target agent's previous state
            state.update(existing_target)
            self._checkpoints = self._agent_checkpoints.get(
                to_agent, deque(maxlen=3)
            )
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
            self._checkpoints = deque(maxlen=3)
            self._agent_checkpoints[to_agent] = self._checkpoints

        # 4. Swap active agent
        state["active_agent"] = to_agent
        state["handoff_task"] = handoff_data.get("task", "")

        # 5. Emit agent_switch
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
                                      handoff_data: dict) -> AgentState:
        """Restore original agent after sub-agent handoff back."""
        if not self._handoff_checkpoint:
            return state

        session_id = state.get("session_id", "default")
        cp = self._handoff_checkpoint
        result_content = ""

        # Get sub-agent's last assistant message as result
        for m in reversed(state.get("messages", [])):
            if m.get("role") == "assistant":
                result_content = m.get("content", "")
                break

        if not result_content:
            result_content = "(handoff completed, no response)"

        # Save current agent's final state
        current_agent = state.get("active_agent", "")
        if current_agent:
            self._agent_states[current_agent] = dict(state)
            self._agent_checkpoints[current_agent] = self._checkpoints
            await self.state_store.put(f"{session_id}/{current_agent}", state)

        # Restore checkpointed messages + replace handoff tool_result
        messages = cp["messages"]
        task = state.get("handoff_task", "")

        # The checkpoint messages end with:
        #   assistant(tool_calls=[handoff_to_sys, ...]) + tool(result={...})
        # Replace the original handoff tool result with sub-agent's response.
        if messages and messages[-1].get("role") == "tool":
            messages[-1]["content"] = result_content

        # Emit agent_switch back
        from_agent = current_agent
        to_agent = cp.get("active_agent", "")
        self._emit("agent_switch", {
            "from": from_agent,
            "to": to_agent,
            "task": "handoff complete",
        }, session_id=session_id, agent_name=to_agent)

        # Restore original agent's checkpoint stack
        self._checkpoints = self._agent_checkpoints.get(
            to_agent, deque(maxlen=3)
        )

        # Swap state
        state["messages"] = messages
        state["active_agent"] = to_agent
        state["current_turn"] = cp.get("current_turn", 0)
        state["interaction_round"] = cp.get("interaction_round", 0)
        state["tool_results"] = {}
        state.pop("handoff_task", None)
        self._handoff_checkpoint = None

        return state

    async def _resolve_tools_for_agent(self, state: AgentState, active: dict) -> list[dict]:
        """Get tool definitions for the active agent, falling back to resolver."""
        # Use active agent's tools list if available, else resolve from filesystem
        active_tools = active.get("tools", [])
        if active_tools:
            return [
                {"name": t.name, "description": t.description,
                 "parameters": t.parameters}
                for t in active_tools
            ]
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

    def _emit(self, event_type: str, data: dict, session_id: str = "", agent_name: str = "") -> None:
        if self.event_bus:
            data["round"] = self._interaction_round
            self.event_bus.emit(AgentEvent(
                type=event_type, data=data, turn=data.get("turn", 0),
                session_id=session_id or data.get("session_id", ""),
                agent_name=agent_name or data.get("agent_name", ""),
            ))

    def _make_event(self, type: str, data: dict, turn: int = 0, session_id: str = "") -> AgentEvent:
        """Create an AgentEvent and publish to EventBus (if set)."""
        data["round"] = self._interaction_round
        event = AgentEvent(type=type, data=data, turn=turn, session_id=session_id)
        if self.event_bus:
            self.event_bus.emit(event)
        return event

    def _repair_state(self, state: AgentState) -> AgentState:
        """Strip incomplete tool_calls sequences that cause 400 errors.

        If the last assistant message has tool_calls but no tool messages follow,
        remove the incomplete assistant message (and any orphaned tool messages).
        """
        msgs = state.get("messages", [])
        if not msgs:
            return state
        # Find the last assistant message with tool_calls
        last_tc_idx = -1
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "assistant" and msgs[i].get("tool_calls"):
                last_tc_idx = i
                break
        if last_tc_idx < 0:
            return state
        # Check if any tool messages follow this assistant message
        has_tool_responses = False
        for i in range(last_tc_idx + 1, len(msgs)):
            if msgs[i].get("role") == "tool":
                has_tool_responses = True
                break
        if not has_tool_responses:
            # Strip from the incomplete assistant message onward
            import logging
            logging.getLogger("arf.engine").warning(
                "Repairing state: stripping incomplete tool_calls at index %d", last_tc_idx
            )
            state["messages"] = msgs[:last_tc_idx]
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

    async def invoke(self, state: AgentState) -> AgentState:
        state = self._repair_state(state)
        session_id = state.get("session_id", "default")
        self._interaction_round = state.get("interaction_round", 0)
        self._emit("session_start", {"session_id": session_id}, session_id=session_id)
        if self.hook_runner:
            await self.hook_runner.fire("session_start", {"session_id": session_id})

        while self.loop_strategy.should_continue(state):
            if self._cancelled():
                self._emit("session_end", {"session_id": session_id, "reason": "cancelled"}, session_id=session_id)
                break
            turn = state.get("current_turn", 0) + 1
            state["current_turn"] = turn

            user_msg = self._last_user_message(state)
            self._emit("user_input", {"content": user_msg, "turn": turn}, session_id=session_id)

            # 1. Memory retrieval
            if self.memory_retriever and self.memory_writer and self.memory_store:
                query = self._last_user_message(state)
                entries = await self.memory_retriever.retrieve(
                    store=self.memory_store,
                    query_context=query,
                    session_id=session_id,
                    max_tokens=2000,
                    top_k=5,
                )
                if entries:
                    state["context_summary"] = "\n".join(
                        f"- {e.content}" for e in entries if e.relevance_score > 0
                    )

            # 2. Route to best model for this turn (before compaction — need model's window size)
            model = state["current_model"]
            if self.model_router:
                model = await self.model_router.route(self._last_user_message(state), state.get("messages", []))
                state["current_model"] = model

            # 2.5 Compaction — after routing (uses selected model's window), before model call
            if self.compaction:
                window = self._model_windows.get(model, 128_000) if hasattr(self, '_model_windows') else 128_000
                if self.compaction.should_compact(state, window_size=window):
                    self._emit("compaction_start", {"turn": turn, "model": model, "msg_count": len(state.get("messages", []))}, session_id=session_id)
                    state = await self.compaction.compact(state)
                    self._emit("compaction_end", {"turn": turn, "msg_count": len(state.get("messages", [])), "summary_len": len(state.get("context_summary", ""))}, session_id=session_id)

            # 3. Get tool definitions — use active agent's tools
            active = self._active_config(state)
            tools = await self._resolve_tools_for_agent(state, active)

            # 4. Build messages & call model
            msgs = [{"role": "system", "content": active["system_prompt"]}]
            if state.get("context_summary"):
                msgs[0]["content"] += f"\n\n## Memory\n{state['context_summary']}"
            msgs.extend(state.get("messages", []))

            if self.hook_runner:
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
                response = await self._call_model(msgs, model, tools=tools)
            except Exception as exc:
                # 降级链最后一级：模型调用失败 → error_policy → fallback 模型
                fallback_model = self._resolve_fallback(model, exc)
                if fallback_model:
                    self._emit("model_call_start", {"model": fallback_model, "turn": turn,
                               "fallback_from": model}, session_id=session_id)
                    response = await self._call_model(msgs, fallback_model, tools=tools)
                    model = fallback_model
                    state["current_model"] = fallback_model
                else:
                    raise
            # Track token usage for next turn's compaction decision
            if isinstance(response, dict) and response.get("usage"):
                state["last_token_usage"] = response["usage"].get("total_tokens", 0)
            self._emit("model_call_end", {"model": model, "turn": turn,
                       "usage": response.get("usage", {}) if isinstance(response, dict) else {},
                       "content": response.get("content", "") if isinstance(response, dict) else ""},
                       session_id=session_id)

            if self.hook_runner:
                self._emit("hook_start", {"event": "post_model_call", "turn": turn}, session_id=session_id)
                results = await self.hook_runner.fire("post_model_call", {"response": response})
                self._emit("hook_end", {"event": "post_model_call", "turn": turn,
                           "count": len(results),
                           "passed": sum(1 for r in results if r.exit_code == 0),
                           "failed": sum(1 for r in results if r.exit_code != 0)},
                           session_id=session_id)
                self._inject_hook_messages(results, state)

            # 4. Guard output
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

            # 5. Parse tool calls
            tool_calls = self._pars_tool_calls(response)
            if not tool_calls:
                state["messages"].append({"role": "assistant", "content": response_text})
                if self.memory_writer and self.memory_store:
                    existing = await self.memory_store.load(session_id)
                    await self.memory_writer.extract_and_write(
                        store=self.memory_store,
                        turn_messages=state["messages"][-4:],
                        existing_entries=existing,
                    )
                await self.state_store.put(session_id, state)
                break

            # Append assistant message with tool_calls before execution
            assistant_tool_calls = [
                {"id": tc.get("id", ""), "type": "function",
                 "function": {"name": tc.get("name", ""), "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)}}
                for tc in tool_calls
            ]
            assistant_msg = {"role": "assistant", "content": response_text, "tool_calls": assistant_tool_calls}
            if isinstance(response, dict) and response.get("reasoning"):
                assistant_msg["reasoning_content"] = response["reasoning"]
            state["messages"].append(assistant_msg)
            # NOTE: do NOT state_store.put() here — tool results not yet appended.
            # Saving incomplete tool_calls sequence causes 400 on next request.

            # 6. Guard tool params + pipeline + permissions + execute
            valid_calls = []
            denied_calls = []
            # Load active skill pipeline from state
            pipeline_data = state.get("active_pipeline")
            import_completed = set(pipeline_data.get("completed", [])) if pipeline_data else set()
            if self.guard_runner:
                for tc in tool_calls:
                    name = tc.get("name", "")
                    params = tc.get("params", {})
                    # Pipeline order check (hard block — framework guarantee)
                    if pipeline_data:
                        from arf.skills.pipeline import SkillPipeline
                        sp = SkillPipeline(pipeline_data.get("steps", []))
                        if not sp.can_execute(name, import_completed):
                            denied_calls.append((name, sp.validation_error(name, import_completed)))
                            continue
                    # Path sandbox check (hard block)
                    gr = await self.guard_runner.check_tool_params(name, params)
                    if not gr.allowed:
                        denied_calls.append((name, gr.reason))
                        continue
                    # Permission check (deny/ask/allow)
                    perm = self.guard_runner.check_tool_permission(name, params)
                    if perm == "deny":
                        denied_calls.append((name, "denied by permission config"))
                        continue
                    if perm == "ask" and not self.approval_enabled:
                        denied_calls.append((name, "requires approval (approval channel not enabled)"))
                        continue
                    valid_calls.append(tc)
            else:
                valid_calls = tool_calls

            # Track completed pipeline steps
            if pipeline_data:
                for tc in valid_calls:
                    import_completed.add(tc.get("name", ""))
                state["active_pipeline"]["completed"] = list(import_completed)

            # Emit denied tool calls as errors
            for name, reason in denied_calls:
                self._emit("tool_call_end", {"tool_name": name, "turn": turn, "id": "",
                           "success": False, "error": f"Blocked: {reason}"},
                           session_id=session_id)

            # 7. Hooks + Transaction + execute
            if self.hook_runner:
                results = await self.hook_runner.fire("pre_tool_exec", {"tool_calls": valid_calls, "turn": turn})
                self._inject_hook_messages(results, state)
            for tc in valid_calls:
                self._emit("tool_call_start", {"tool_name": tc.get("name", ""), "turn": turn,
                           "id": tc.get("id", ""),
                           "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)},
                           session_id=session_id)
            tx = None
            if self.transaction_ctx:
                tx = await self.transaction_ctx.begin(session_id, turn)
            agent_mode = state.get("active_agent", "")
            results = await self.tool_executor.execute(valid_calls, agent_mode=agent_mode)
            for tc in valid_calls:
                r = results.get(tc.get("id", ""))
                self._emit("tool_call_end", {"tool_name": tc.get("name", ""), "turn": turn, "id": tc.get("id", ""),
                          "success": r.success if r else False, "duration_ms": r.duration_ms if r else 0,
                          "result": str(r.data)[:500] if r and r.success and r.data else "",
                          "error": str(r.error)[:500] if r and r.error else ""},
                          session_id=session_id)
            if self.transaction_ctx and tx:
                all_ok = all(r.success for r in results.values())
                if all_ok:
                    await self.transaction_ctx.commit(tx)
                else:
                    await self.transaction_ctx.rollback(tx, Exception("tool failure"))

            if self.hook_runner:
                hook_results = await self.hook_runner.fire("post_tool_exec", {"tool_calls": valid_calls, "results": {k: {"success": v.success} for k, v in results.items()}, "turn": turn})
                self._inject_hook_messages(hook_results, state)

            # 8. Add results to messages (with tool output summarization)
            for tc in valid_calls:
                r = results.get(tc.get("id", ""))
                if r:
                    content = str(r.data) if r.success else f"Error: {r.error}"
                    if r.success and self.compaction and content:
                        content = await self.compaction.summarize_tool_output(
                            tc.get("name", "unknown"), content, turn
                        )
                    state["messages"].append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": content,
                    })
            state["tool_results"] = {
                k: {"success": v.success, "data": v.data, "error": v.error}
                for k, v in results.items()
            }

            # --- Handoff detection (invoke) ---
            if self._handoff_manager and self._handoff_manager.has_rules:
                handoff_signal = self._handoff_manager.detect(state["tool_results"])
                if handoff_signal:
                    state = await self._execute_handoff(state, handoff_signal, model)
                    if state.get("handoff_error"):
                        state["messages"].append({
                            "role": "tool",
                            "tool_call_id": handoff_signal.get("tool_call_id", ""),
                            "content": f"Handoff failed: {state['handoff_error']}",
                        })
                        del state["handoff_error"]
                        await self.state_store.put(session_id, state)
                    continue

            # 9. Memory write — after turn
            if self.memory_writer and self.memory_store:
                existing = await self.memory_store.load(session_id)
                await self.memory_writer.extract_and_write(
                    store=self.memory_store,
                    turn_messages=state["messages"][-4:],
                    existing_entries=existing,
                )

            # 10. Checkpoint
            await self.state_store.put(session_id, state)

            if turn >= active["max_turns"]:
                break

        if self.hook_runner:
            await self.hook_runner.fire("session_end", {"session_id": session_id})
        self._emit("session_end", {"session_id": session_id}, session_id=session_id)
        return state

    async def astream(self, state: AgentState):
        """Streaming execution — yields AgentEvent at each step of the loop.
        Uses _stream_model for token-level streaming if available,
        falls back to _call_model otherwise."""
        state = self._repair_state(state)
        session_id = state.get("session_id", "default")
        self._interaction_round = state.get("interaction_round", 0)
        yield self._make_event(type="session_start", data={"session_id": session_id},
                         session_id=session_id)
        if self.hook_runner:
            await self.hook_runner.fire("session_start", {"session_id": session_id})

        while self.loop_strategy.should_continue(state):
            if self._cancelled():
                yield self._make_event(type="session_end",
                                 data={"session_id": session_id, "reason": "cancelled"},
                                 session_id=session_id)
                break
            turn = state.get("current_turn", 0) + 1
            state["current_turn"] = turn

            user_msg = self._last_user_message(state)
            yield self._make_event(type="user_input",
                             data={"content": user_msg, "turn": turn},
                             turn=turn, session_id=session_id)

            # Memory retrieval before this turn
            if self.memory_retriever and self.memory_writer and self.memory_store:
                entries = await self.memory_retriever.retrieve(
                    store=self.memory_store,
                    query_context=user_msg,
                    session_id=session_id,
                    max_tokens=2000,
                    top_k=5,
                )
                if entries:
                    state["context_summary"] = "\n".join(
                        f"- {e.content}" for e in entries if e.relevance_score > 0
                    )

            if not self._call_model:
                break

            # Route to best model for this turn (before compaction — need model's window)
            model = state["current_model"]
            if self.model_router:
                model = await self.model_router.route(self._last_user_message(state), state.get("messages", []))
                state["current_model"] = model

            # Compaction — after routing (uses selected model's window), before model call
            if self.compaction:
                window = self._model_windows.get(model, 128_000) if hasattr(self, '_model_windows') else 128_000
                if self.compaction.should_compact(state, window_size=window):
                    yield self._make_event(type="compaction_start",
                                     data={"turn": turn, "model": model, "msg_count": len(state.get("messages", []))},
                                     turn=turn, session_id=session_id)
                    state = await self.compaction.compact(state)
                    yield self._make_event(type="compaction_end",
                                     data={"turn": turn, "msg_count": len(state.get("messages", [])),
                                           "summary_len": len(state.get("context_summary", ""))},
                                     turn=turn, session_id=session_id)

            # Get tool definitions for this turn
            tools: list[dict] = []
            if self.tool_resolver:
                active = self._active_config(state)
                tools = await self._resolve_tools_for_agent(state, active)

            msgs = [{"role": "system", "content": active["system_prompt"]}]
            summary = state.get("context_summary", "")
            if summary:
                msgs[0]["content"] += f"\n\n## Memory\n{summary}"
            msgs.extend(state.get("messages", []))

            yield self._make_event(type="model_call_start",
                             data={"model": model, "turn": turn},
                             turn=turn, session_id=session_id)

            if self._stream_model:
                # Token-level streaming
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
                                             turn=turn, session_id=session_id)
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
                            yield self._make_event(type="error",
                                             data={"code": chunk.get("code", 0),
                                                   "detail": chunk.get("detail", "")},
                                             turn=turn, session_id=session_id)
                            resp = {"content": "", "tool_calls": []}
                            break
                    else:
                        resp = {"content": full_text, "tool_calls": stream_tool_calls, "reasoning": full_reasoning}
                except Exception as exc:
                    # Streaming failed, try fallback with sync call
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
                # Sync fallback
                try:
                    resp = await self._call_model(msgs, model, tools=tools)
                except Exception as exc:
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

            yield self._make_event(type="model_call_end",
                             data={"model": model, "turn": turn,
                                   "content": resp.get("content", ""),
                                   "usage": stream_usage},
                             turn=turn, session_id=session_id)

            # Track token usage for next turn's compaction decision
            if stream_usage and stream_usage.get("total_tokens", 0) > 0:
                state["last_token_usage"] = stream_usage["total_tokens"]

            tool_calls = self._pars_tool_calls(resp)
            if not tool_calls:
                state["messages"].append({"role": "assistant", "content": resp.get("content", "")})
                await self.state_store.put(session_id, state)
                if self.memory_writer and self.memory_store:
                    existing = await self.memory_store.load(session_id)
                    await self.memory_writer.extract_and_write(
                        store=self.memory_store,
                        turn_messages=state["messages"][-4:],
                        existing_entries=existing,
                    )
                break

            # Append assistant message with tool_calls BEFORE executing tools
            assistant_tool_calls = [
                {"id": tc.get("id", ""), "type": "function",
                 "function": {"name": tc.get("name", ""), "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)}}
                for tc in tool_calls
            ]
            assistant_msg: dict = {"role": "assistant", "content": resp.get("content") or "", "tool_calls": assistant_tool_calls}
            # DeepSeek requires reasoning_content to be passed back in thinking mode
            if resp.get("reasoning"):
                assistant_msg["reasoning_content"] = resp["reasoning"]
            state["messages"].append(assistant_msg)
            await self.state_store.put(session_id, state)

            # Guard tool params + pipeline + permissions (streaming path)
            valid_calls = []
            denied_calls = []
            pipeline_data = state.get("active_pipeline")
            s_completed = set(pipeline_data.get("completed", [])) if pipeline_data else set()
            if self.guard_runner:
                for tc in tool_calls:
                    name = tc.get("name", "")
                    params = tc.get("params", {})
                    # Pipeline order check (hard block)
                    if pipeline_data:
                        from arf.skills.pipeline import SkillPipeline
                        sp = SkillPipeline(pipeline_data.get("steps", []))
                        if not sp.can_execute(name, s_completed):
                            denied_calls.append((name, sp.validation_error(name, s_completed)))
                            continue
                    gr = await self.guard_runner.check_tool_params(name, params)
                    if not gr.allowed:
                        denied_calls.append((name, gr.reason))
                        yield self._make_event(
                            type="guard_block",
                            data={"tool_name": name, "guard": "path_check", "reason": gr.reason},
                            turn=turn, session_id=session_id,
                        )
                        continue
                    perm = self.guard_runner.check_tool_permission(name, params)
                    if perm == "deny":
                        denied_calls.append((name, "denied by permission config"))
                        yield self._make_event(
                            type="guard_block",
                            data={"tool_name": name, "guard": "permission", "reason": "denied by config"},
                            turn=turn, session_id=session_id,
                        )
                        continue
                    if perm == "ask":
                        needs_approval = self.approval_enabled and (
                            not self._approval_allowlist or name in self._approval_allowlist
                        )
                        if needs_approval:
                            decision_id = f"{session_id}_{name}_{id(tc)}"
                            yield self._make_event(
                                type="approval_required",
                                data={"decision_id": decision_id, "tool_name": name, "params": params},
                                turn=turn, session_id=session_id,
                            )
                            # Wait for user decision
                            approval_evt = asyncio.Event()
                            self._pending_approvals[decision_id] = approval_evt
                            try:
                                await asyncio.wait_for(approval_evt.wait(), timeout=60.0)
                            except asyncio.TimeoutError:
                                self._pending_approvals.pop(decision_id, None)
                                self._approval_results.pop(decision_id, None)
                                denied_calls.append((name, "approval timed out"))
                                yield self._make_event(
                                    type="approval_resolved",
                                    data={"decision_id": decision_id, "tool_name": name,
                                          "approved": False, "reason": "timeout"},
                                    turn=turn, session_id=session_id,
                                )
                                continue
                            approved = self._approval_results.pop(decision_id, False)
                            if not approved:
                                denied_calls.append((name, "denied by user"))
                                yield self._make_event(
                                    type="approval_resolved",
                                    data={"decision_id": decision_id, "tool_name": name,
                                          "approved": False, "reason": "denied by user"},
                                    turn=turn, session_id=session_id,
                                )
                                continue
                            # Approved — fall through to valid_calls
                            yield self._make_event(
                                type="approval_resolved",
                                data={"decision_id": decision_id, "tool_name": name,
                                      "approved": True, "reason": "approved"},
                                turn=turn, session_id=session_id,
                            )
                        else:
                            denied_calls.append((name, "requires approval (approval channel not enabled)"))
                            continue
                    yield self._make_event(
                        type="guard_pass",
                        data={"tool_name": name},
                        turn=turn, session_id=session_id,
                    )
                    valid_calls.append(tc)
            else:
                valid_calls = tool_calls

            # Track completed pipeline steps
            if pipeline_data:
                for tc in valid_calls:
                    s_completed.add(tc.get("name", ""))
                state["active_pipeline"]["completed"] = list(s_completed)

            for name, reason in denied_calls:
                yield self._make_event(type="tool_call_end",
                                 data={"tool_name": name, "turn": turn, "id": "",
                                       "success": False, "error": f"Blocked: {reason}"},
                                 turn=turn, session_id=session_id)

            if self.hook_runner:
                h_results = await self.hook_runner.fire("pre_tool_exec", {"tool_calls": valid_calls, "turn": turn})
                self._inject_hook_messages(h_results, state)
            for tc in valid_calls:
                yield self._make_event(type="tool_call_start",
                                 data={"tool_name": tc.get("name", ""), "turn": turn,
                                       "id": tc.get("id", ""),
                                       "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)},
                                 turn=turn, session_id=session_id)
            agent_mode = state.get("active_agent", "")
            results = await self.tool_executor.execute(valid_calls, agent_mode=agent_mode)
            for tc in valid_calls:
                r = results.get(tc.get("id", ""))
                yield self._make_event(type="tool_call_end",
                                 data={"tool_name": tc.get("name", ""), "turn": turn, "id": tc.get("id", ""),
                                       "success": r.success if r else False,
                                       "duration_ms": r.duration_ms if r else 0,
                                       "result": str(r.data)[:500] if r and r.success and r.data else "",
                                       "error": str(r.error)[:500] if r and r.error else ""},
                                 turn=turn, session_id=session_id)
                if r:
                    content = str(r.data) if r.success else f"Error: {r.error}"
                    if r.success and self.compaction and content:
                        content = await self.compaction.summarize_tool_output(
                            tc.get("name", "unknown"), content, turn
                        )
                    state["messages"].append({"role": "tool", "tool_call_id": tc["id"],
                                              "content": content})
            if self.hook_runner:
                hr = await self.hook_runner.fire("post_tool_exec", {"tool_calls": valid_calls, "results": {k: {"success": v.success} for k, v in results.items()}, "turn": turn})
                self._inject_hook_messages(hr, state)

            state["tool_results"] = {
                k: {"success": v.success, "data": v.data, "error": v.error}
                for k, v in results.items()
            }

            # --- Handoff detection (astream) ---
            if self._handoff_manager and self._handoff_manager.has_rules:
                handoff_signal = self._handoff_manager.detect(state["tool_results"])
                if handoff_signal:
                    state = await self._execute_handoff(state, handoff_signal, model)
                    if state.get("handoff_error"):
                        state["messages"].append({
                            "role": "tool",
                            "tool_call_id": handoff_signal.get("tool_call_id", ""),
                            "content": f"Handoff failed: {state['handoff_error']}",
                        })
                        del state["handoff_error"]
                        yield self._make_event(type="error",
                                         data={"detail": f"Handoff failed: {state.get('handoff_error', '')}"},
                                         turn=turn, session_id=session_id)
                    await self.state_store.put(session_id, state)
                    continue

            await self.state_store.put(session_id, state)

            # Memory extraction after tool execution turn
            if self.memory_writer and self.memory_store:
                existing = await self.memory_store.load(session_id)
                await self.memory_writer.extract_and_write(
                    store=self.memory_store,
                    turn_messages=state["messages"][-4:],
                    existing_entries=existing,
                )

            if turn >= active["max_turns"]:
                break

        if self.hook_runner:
            await self.hook_runner.fire("session_end", {"session_id": session_id})
        yield self._make_event(type="session_end", data={"session_id": session_id},
                         session_id=session_id)
