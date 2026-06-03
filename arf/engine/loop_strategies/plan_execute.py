"""PlanExecuteStrategy — plan → execute → observe → replan loop."""
from arf.core.protocols import LoopStrategy
from arf.core.state import AgentState


class PlanExecuteStrategy:
    """Multi-phase loop: plan → execute → observe → replan → summarize.

    Phases managed internally:
      plan      — model generates a plan
      execute   — model or tools execute the current step
      observe   — inspect tool results, then advance to next step
      replan    — model revises the plan if divergence detected
      summarize — produce final summary, loop terminates
    """

    PHASES = ("plan", "execute", "observe", "replan", "summarize")

    def __init__(self, max_turns: int = 50) -> None:
        self.max_turns = max_turns
        self._phase = "plan"
        self._current_step_index = 0
        self._plan_steps: list[dict] = []

    @property
    def current_phase(self) -> str:
        return self._phase

    def should_continue(self, state: AgentState) -> bool:
        if state.get("current_turn", 0) >= self.max_turns:
            return False
        if self._phase == "summarize":
            return False
        return True

    def should_break(self, state: AgentState) -> bool:
        return not self.should_continue(state)

    def next_step(self, state: AgentState) -> str:
        if self._phase == "plan":
            self._phase = "execute"
            return "call_model"      # model generates the plan
        if self._phase == "execute":
            if self._current_step_index < len(self._plan_steps):
                step = self._plan_steps[self._current_step_index]
                if step.get("tool"):
                    return "execute_tools"
                return "call_model"
            self._phase = "summarize"
            return "call_model"
        if self._phase == "observe":
            self._current_step_index += 1
            if self._current_step_index >= len(self._plan_steps):
                self._phase = "summarize"
            else:
                self._phase = "execute"
            return "call_model"
        return "call_model"

    def on_transition(self, event: str, ctx) -> None:
        msgs = ctx.state.get("messages", []) if hasattr(ctx, 'state') else []
        if event == "turn_end" and self._phase == "execute":
            last = msgs[-1] if msgs else {}
            if last.get("role") == "tool":
                self._phase = "observe"
