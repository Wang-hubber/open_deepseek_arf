"""ReActStrategy — two gates (should_continue / should_break) + dispatch.

Both gates read self.max_turns, which the engine mutates from
_active_config() so multi-agent scenarios use the correct per-agent limit.

next_step() is reserved for plan_execute / multi-phase loop patterns;
the current engine hardcodes the ReAct ordering and does not call it.
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
        """Return the next phase: 'call_model' or 'execute_tools'.

        Reserved for plan_execute / multi-phase loop patterns.
        Not currently called by GraphEngine — the engine hardcodes
        the ReAct ordering (model → tools → model).
        """
        last = state["messages"][-1]
        if last.get("role") == "tool":
            return "call_model"
        return "execute_tools"
