"""Protocols for resources domain."""
from typing import Protocol
from dataclasses import dataclass
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict


class ToolResolver(Protocol):
    """Engine's single interface to tools."""
    async def get_tool_definitions(
        self, query_context: str, top_k: int = 10,
    ) -> list[ToolDefinition]: ...
    async def execute(self, tool_name: str, params: dict) -> ToolResult: ...


class ToolProvider(Protocol):
    """Internal: tool source abstraction."""
    async def list_tools(self) -> list[ToolConfig]: ...
    async def resolve(self, name: str) -> ToolConfig | None: ...


class ToolRetriever(Protocol):
    """Internal: semantic retrieval of top-k tools."""
    async def retrieve(
        self, query_context: str, available_tools: list[ToolConfig], top_k: int = 10,
    ) -> list[ToolConfig]: ...


class ToolBackend(Protocol):
    """Internal: execution backend binding."""
    async def execute(self, tool_config: ToolConfig, params: dict) -> ToolResult: ...
