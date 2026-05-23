"""ReActStrategy — standard Think-Act-Observe loop."""
from arf.core.protocols import LoopStrategy
from arf.core.state import AgentState


class ReActStrategy:
    def __init__(self, max_turns: int = 50) -> None:
        self.max_turns = max_turns

    def should_continue(self, state: AgentState) -> bool:
        turn = state.get("current_turn", 0)
        return turn < self.max_turns

    def next_step(self, state: AgentState) -> str:
        msgs = state.get("messages", [])
        if not msgs:
            return "call_model"
        last = msgs[-1]
        if last.get("role") == "tool":
            return "call_model"
        return "execute_tools"
