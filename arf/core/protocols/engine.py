"""Protocols for engine domain."""
from typing import Protocol
from arf.core.state import AgentState, TurnContext
from arf.core.results import ToolResult, ErrorAction


class LoopStrategy(Protocol):
    """Agent execution loop pattern — entry gate, exit gate, step dispatch.

    next_step() is reserved for plan_execute / multi-phase loop patterns;
    the current engine hardcodes the ReAct ordering (model → tools → model).

    current_phase exposes the internal phase for monitoring/UI.
    on_transition() is called at turn_end so the strategy can update
    internal state based on what just happened.
    """
    def should_continue(self, state: AgentState) -> bool: ...
    def should_break(self, state: AgentState) -> bool: ...
    def next_step(self, state: AgentState) -> str: ...

    @property
    def current_phase(self) -> str:
        """Read-only phase label for monitoring / UI."""
        ...

    def on_transition(self, event: str, ctx) -> None:
        """Called by engine at turn_end. Strategy updates internal state."""
        ...


class StateStore(Protocol):
    """Persist/restore AgentState at checkpoint boundaries."""
    async def put(self, session_id: str, state: AgentState) -> None: ...
    async def get(self, session_id: str) -> AgentState | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def list_sessions(self) -> list[str]: ...


class ToolExecutor(Protocol):
    """Execute multiple tool_calls with concurrency control."""
    async def execute(
        self,
        tool_calls: list[dict],
        strategy: str = "parallel",
        max_concurrency: int = 5,
    ) -> dict[str, ToolResult]: ...


class Planner(Protocol):
    """Plan generation, progress tracking, divergence detection, revision."""
    async def generate_plan(self, task: str, context: TurnContext, tools: list[dict]) -> dict: ...
    async def update_progress(self, plan: dict, completed_step: dict, result: ToolResult) -> dict: ...
    async def detect_divergence(self, plan: dict, state: AgentState) -> dict: ...
    async def revise(self, plan: dict, divergence: dict, context: TurnContext) -> dict: ...
