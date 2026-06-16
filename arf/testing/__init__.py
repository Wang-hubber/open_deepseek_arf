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


class InMemoryToolExecutor:
    """ConcurrentToolExecutor for testing. Records all calls."""
    def __init__(self, tools: dict | None = None) -> None:
        self.calls: list = []
        self.results: dict[str, dict] = {}
        # tools param accepted for caller convenience (not yet used internally)

    def set_result(self, call_id: str, success: bool = True, data: dict | None = None, error: str | None = None) -> None:
        self.results[call_id] = {"success": success, "data": data or {}, "error": error}

    async def execute(self, tool_calls: list[dict], strategy: str = "parallel", max_concurrency: int = 5):
        self.calls.extend(tool_calls)
        from arf.core.results import ToolResult
        results = {}
        for tc in tool_calls:
            preset = self.results.get(tc["id"], {"success": True, "data": {"result": "ok"}, "error": None})
            results[tc["id"]] = ToolResult(tool_name=tc.get("name", tc["id"]),
                                            success=preset["success"],
                                            data=preset["data"],
                                            error=preset["error"])
        return results

    def reset(self) -> None:
        self.calls.clear()
        self.results.clear()


__all__ = [
    "InMemoryStateStore", "InMemoryEventBus",
    "InMemoryGuardRunner",
    "InMemoryToolResolver", "InMemoryToolExecutor",
    "QueuedTaskDelegator",
]
