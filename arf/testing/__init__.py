"""arf.testing -- InMemory test doubles for all core Protocols.

Usage:
    from arf.testing import InMemoryStateStore, InMemoryEventBus, ...
"""
from arf.communication.queued_delegator import QueuedTaskDelegator
from arf.engine.checkpoint import InMemoryStateStore
from arf.event_bus import InMemoryEventBus


class InMemoryGuardRunner:
    """Pass-through GuardRunner for testing."""
    @staticmethod
    async def check_input(message, context):
        from arf.core.results import GuardResult
        return GuardResult(allowed=True)

    @staticmethod
    async def check_output(message, context):
        from arf.core.results import GuardResult
        return GuardResult(allowed=True)

    @staticmethod
    async def check_tool_params(tool_name, params):
        from arf.core.results import GuardResult
        return GuardResult(allowed=True)


class InMemoryToolResolver:
    """Preset-tool ToolResolver for testing."""
    def __init__(self, tools: dict | None = None) -> None:
        from arf.core.protocols.resources import ToolDefinition
        self._tools: dict[str, ToolDefinition] = tools or {}
        self.calls: list = []

    def add(self, name: str, description: str = "", parameters: dict | None = None) -> None:
        from arf.core.protocols.resources import ToolDefinition
        self._tools[name] = ToolDefinition(name=name, description=description,
                                            parameters=parameters or {})

    async def get_tool_definitions(self, query_context: str, top_k: int = 10):
        return list(self._tools.values())

    async def execute(self, tool_name: str, params: dict):
        self.calls.append({"tool_name": tool_name, "params": params})
        from arf.core.results import ToolResult
        if tool_name in self._tools:
            return ToolResult(tool_name=tool_name, success=True, data={"result": "ok"})
        return ToolResult(tool_name=tool_name, success=False, error=f"Tool '{tool_name}' not found")

    def reset(self) -> None:
        self.calls.clear()



__all__ = [
    "InMemoryStateStore", "InMemoryEventBus",
    "InMemoryGuardRunner",
    "InMemoryToolResolver",
    "QueuedTaskDelegator",
]
