"""arf.testing -- InMemory test doubles for all core Protocols.

Usage:
    from arf.testing import InMemoryStateStore, InMemoryEventBus, ...
"""
from arf.engine.checkpoint import InMemoryStateStore
from arf.event_bus import InMemoryEventBus


class InMemoryMemoryStore:
    """Dict-backed MemoryStore for testing."""
    def __init__(self) -> None:
        self.entries: list = []
        self.saves: list = []
        self.deletes: list = []

    async def save(self, entry) -> None:
        self.saves.append(entry)
        self.entries = [e for e in self.entries if e.id != entry.id]
        self.entries.append(entry)

    async def load(self, session_id: str):
        return list(self.entries)

    async def delete(self, entry_id: str) -> None:
        self.deletes.append(entry_id)
        self.entries = [e for e in self.entries if e.id != entry_id]

    def reset(self) -> None:
        self.entries.clear()
        self.saves.clear()
        self.deletes.clear()


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


class InMemoryApprovalChannel:
    """Auto-approve ApprovalChannel for testing."""
    def __init__(self) -> None:
        self.responses: list = []

    async def send(self, request):
        self.responses.append(request)
        return "test_approval_id"

    async def wait(self, approval_id: str, timeout: int):
        from arf.core.results import ApprovalResponse
        return ApprovalResponse(action="approve")

    def reset(self) -> None:
        self.responses.clear()


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
    def __init__(self) -> None:
        self.calls: list = []
        self.results: dict[str, dict] = {}

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


class InMemoryMemoryRetriever:
    """Recent-first memory retriever for testing."""
    def __init__(self, seed_entries: list | None = None) -> None:
        self.seed_entries = seed_entries or []

    async def retrieve(self, store, query_context: str, session_id: str, max_tokens: int = 2000, top_k: int = 5):
        return self.seed_entries[:top_k]


__all__ = [
    "InMemoryStateStore", "InMemoryEventBus",
    "InMemoryMemoryStore", "InMemoryGuardRunner", "InMemoryApprovalChannel",
    "InMemoryToolResolver", "InMemoryToolExecutor", "InMemoryMemoryRetriever",
]
