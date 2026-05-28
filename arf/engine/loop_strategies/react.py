"""ReActStrategy — two gates (should_continue / should_break) + dispatch.

Both gates read self.max_turns, which the engine mutates from
_active_config() so multi-agent scenarios use the correct per-agent limit.

next_step() drives the main loop dispatch. The engine calls it at the
top of each iteration; different strategies return different step names
so the framework can switch control modes without changing the engine.
"""
from arf.core.protocols import LoopStrategy
from arf.core.state import AgentState


class ReActStrategy:
    def __init__(self, max_turns: int = 50) -> None:
        self.max_turns = max_turns

    def should_continue(self, state: AgentState) -> bool:
        """Entry gate — false means the loop body is skipped entirely."""
        return state.get("current_turn", 0) < self.max_turns

    def should_break(self, state: AgentState) -> bool:
        """Exit gate — true means exit the loop after this turn completes.

        Currently only monitors turn count. Future extensions may also
        check token budget, wall-clock time, or tool-call count.
        """
        return state.get("current_turn", 0) >= self.max_turns

    def next_step(self, state: AgentState) -> str:
        """Return the next phase for the engine to execute.

        ReAct cycle:
          user message  → call_model   (think)
          model returns tool_calls → execute_tools (act)
          tool results  → call_model   (observe → think)
          model returns text → call_model → parse → break (no dispatch needed)

        PlanExecuteStrategy would return "plan" / "execute" / "replan".
        """
        msgs = state.get("messages", [])
        if not msgs:
            return "call_model"
        last = msgs[-1]
        role = last.get("role", "")
        if role in ("user", "system"):
            return "call_model"
        if role == "assistant" and last.get("tool_calls"):
            return "execute_tools"
        # tool result → think
        return "call_model"
