"""GraphEngine — DI-driven Agent execution loop."""
from typing import Callable
from arf.core.protocols import (
    LoopStrategy, StateStore, ToolExecutor, TransactionContext, Planner,
    ToolResolver, MemoryRetriever, MemoryWriter, HookRunner,
    GuardRunner, EventBus, ErrorPolicy,
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
        memory_retriever: MemoryRetriever | None = None,
        memory_writer: MemoryWriter | None = None,
        hook_runner: HookRunner | None = None,
        guard_runner: GuardRunner | None = None,
        event_bus: EventBus | None = None,
        error_policy: ErrorPolicy | None = None,
        call_model: Callable | None = None,
        stream_model: Callable | None = None,
        system_prompt: str = "",
        max_turns: int = 50,
    ):
        self.loop_strategy = loop_strategy
        self.state_store = state_store
        self.tool_executor = tool_executor
        self.tool_resolver = tool_resolver
        self.transaction_ctx = transaction_ctx
        self.planner = planner
        self.memory_retriever = memory_retriever
        self.memory_writer = memory_writer
        self.hook_runner = hook_runner
        self.guard_runner = guard_runner
        self.event_bus = event_bus
        self.error_policy = error_policy
        self._call_model = call_model
        self._stream_model = stream_model
        self._system_prompt = system_prompt
        self._max_turns = max_turns

    def set_call_model(self, call_model) -> None:
        """Late-binding injection of the model API call function."""
        self._call_model = call_model

    def _emit(self, event_type: str, data: dict) -> None:
        if self.event_bus:
            self.event_bus.emit(AgentEvent(type=event_type, data=data, turn=data.get("turn", 0)))

    def _last_user_message(self, state: AgentState) -> str:
        for m in reversed(state.get("messages", [])):
            if m.get("role") == "user":
                return m.get("content", "")
        return ""

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
        session_id = state.get("session_id", "default")
        self._emit("session_start", {"session_id": session_id})

        while self.loop_strategy.should_continue(state):
            turn = state.get("current_turn", 0) + 1
            state["current_turn"] = turn

            # 1. Memory retrieval — before compaction
            if self.memory_retriever and self.memory_writer:
                query = self._last_user_message(state)
                from arf.core.protocols.memory import MemoryStore
                class _DummyStore:
                    async def load(self, sid): return []
                    async def save(self, e): pass
                    async def delete(self, eid): pass
                entries = await self.memory_retriever.retrieve(
                    store=_DummyStore(),
                    query_context=query,
                    session_id=session_id,
                    max_tokens=2000,
                    top_k=5,
                )
                if entries:
                    state["context_summary"] = "\n".join(
                        f"- {e.content}" for e in entries if e.relevance_score > 0
                    )

            # 2. Get tool definitions
            tools = []
            if self.tool_resolver:
                tools = await self.tool_resolver.get_tool_definitions(
                    self._last_user_message(state), top_k=10
                )

            # 3. Build messages & call model
            msgs = [{"role": "system", "content": self._system_prompt}]
            if state.get("context_summary"):
                msgs[0]["content"] += f"\n\n## Memory\n{state['context_summary']}"
            msgs.extend(state.get("messages", []))

            if self.hook_runner:
                await self.hook_runner.fire("pre_model_call", {"messages": msgs})

            if not self._call_model:
                break
            response = await self._call_model(msgs, state["current_model"])

            if self.hook_runner:
                await self.hook_runner.fire("post_model_call", {"response": response})

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
                break

            # 6. Guard tool params + execute
            valid_calls = []
            if self.guard_runner:
                for tc in tool_calls:
                    gr = await self.guard_runner.check_tool_params(tc.get("name", ""), tc.get("params", {}))
                    if gr.allowed:
                        valid_calls.append(tc)
            else:
                valid_calls = tool_calls

            # 7. Transaction + execute
            tx = None
            if self.transaction_ctx:
                tx = await self.transaction_ctx.begin(session_id, turn)
            results = await self.tool_executor.execute(valid_calls)
            if self.transaction_ctx and tx:
                all_ok = all(r.success for r in results.values())
                if all_ok:
                    await self.transaction_ctx.commit(tx)
                else:
                    await self.transaction_ctx.rollback(tx, Exception("tool failure"))

            # 8. Add results to messages
            for tc in valid_calls:
                r = results.get(tc.get("id", ""))
                if r:
                    state["messages"].append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": str(r.data) if r.success else f"Error: {r.error}"
                    })
            state["tool_results"] = {
                k: {"success": v.success, "data": v.data, "error": v.error}
                for k, v in results.items()
            }

            # 9. Memory write — after turn
            if self.memory_writer:
                await self.memory_writer.extract_and_write(
                    store=_DummyStore(),
                    turn_messages=state["messages"][-4:],
                    existing_entries=[],
                )

            # 10. Checkpoint
            await self.state_store.put(session_id, state)

            if turn >= self._max_turns:
                break

        self._emit("session_end", {"session_id": session_id})
        return state

    async def astream(self, state: AgentState):
        """Async streaming execution — yields AgentEvent at each step."""
        yield state
