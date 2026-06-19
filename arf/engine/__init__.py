"""ARF Engine — Agent execution loop, state management, and tool execution."""
from arf.engine.control_plane import ControlPlane, SessionAbortedError, MessageContractError
from arf.engine.checkpoint import InMemoryStateStore, FileStateStore
from arf.engine.tool_executor import ConcurrentToolExecutor
from arf.engine.compat import collect_response, collect_events, drain_astream, PrimitiveHookAdapter

# Alias: ControlPlane IS the PrimitiveEngine during transition
PrimitiveEngine = ControlPlane

__all__ = [
    "ControlPlane",
    "PrimitiveEngine",
    "SessionAbortedError",
    "MessageContractError",
    "InMemoryStateStore",
    "FileStateStore",
    "ConcurrentToolExecutor",
    "collect_response",
    "collect_events",
    "drain_astream",
    "PrimitiveHookAdapter",
]
