"""Primitive types — DEPRECATED: use arf.agent.state (AgentState, WaitItem, ModelResult)."""
import warnings
warnings.warn("arf.core.primitives is deprecated. Use arf.agent.state.", DeprecationWarning, stacklevel=2)

from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from arf.core.plugin_context import PluginContext


class Primitive(str, Enum):
    """Four primitives that govern all agent behavior."""
    INPUT = "input"     # Information enters the system
    ACTION = "action"   # System performs operations (model call, tool exec)
    OUTPUT = "output"   # System produces results (events, text, tool results)
    WAIT = "wait"       # System waits for external signal (HITL, subagent, peer)


class Level(str, Enum):
    """Three lifecycle levels."""
    SESSION = "session"
    ROUND = "round"
    TURN = "turn"


@runtime_checkable
class PrimitiveHandler(Protocol):
    """Protocol for plugins that handle primitive transitions natively.

    Each handler receives a PluginContext with `primitive` and `level`
    fields set to the current phase. Plugins implement the methods
    they care about; unimplemented methods default to no-ops via
    the adapter layer.
    """
    name: str

    async def on_input(self, level: Level, ctx: "PluginContext") -> None: ...
    async def on_action_start(self, level: Level, ctx: "PluginContext") -> None: ...
    async def on_action_end(self, level: Level, ctx: "PluginContext") -> None: ...
    async def on_output(self, level: Level, ctx: "PluginContext") -> None: ...
    async def on_wait_start(self, level: Level, ctx: "PluginContext") -> None: ...
    async def on_wait_end(self, level: Level, ctx: "PluginContext") -> None: ...
    async def on_error(
        self, level: Level, ctx: "PluginContext", exc: Exception,
    ) -> None: ...
