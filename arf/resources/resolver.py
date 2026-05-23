"""DefaultToolResolver — wraps Provider + optional Retriever + Backend."""
from arf.core.protocols.resources import ToolDefinition, ToolProvider, ToolRetriever, ToolBackend
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult


class DefaultToolResolver:
    def __init__(
        self,
        providers: list[ToolProvider],
        retriever: ToolRetriever | None = None,
        backend: ToolBackend | None = None,
    ) -> None:
        self._providers = providers
        self._retriever = retriever
        self._backend = backend

    async def get_tool_definitions(
        self, query_context: str, top_k: int = 10,
    ) -> list[ToolDefinition]:
        all_tools: list[ToolConfig] = []
        for p in self._providers:
            all_tools.extend(await p.list_tools())
        if self._retriever and len(all_tools) > top_k:
            all_tools = await self._retriever.retrieve(query_context, all_tools, top_k)
        return [
            ToolDefinition(name=t.name, description=t.description, parameters=t.parameters)
            for t in all_tools
        ]

    async def execute(self, tool_name: str, params: dict) -> ToolResult:
        for p in self._providers:
            cfg = await p.resolve(tool_name)
            if cfg:
                if hasattr(p, "execute"):
                    return await p.execute(tool_name, params)
        return ToolResult(tool_name=tool_name, success=False, error=f"Tool '{tool_name}' not found")

    async def reload(self) -> None:
        """Reload all providers — clears cached tool lists for re-scan."""
        for p in self._providers:
            if hasattr(p, "_tools"):
                p._tools.clear()
            if hasattr(p, "_functions"):
                p._functions.clear()
