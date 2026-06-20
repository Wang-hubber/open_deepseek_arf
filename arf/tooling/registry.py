"""ToolRegistry — DEPRECATED. Use McpClientManager from arf.mcp.client_manager instead.

Aggregate tool definitions from directory, MCP, and kernel sources.
"""
from __future__ import annotations
from typing import Any


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, dict] = {}       # name -> tool_def
        self._executors: dict[str, Any] = {}    # name -> callable

    def register(self, name: str, definition: dict, executor: Any) -> None:
        self._tools[name] = definition
        self._executors[name] = executor

    def register_batch(self, tools: list[dict], executor_map: dict[str, Any]) -> None:
        for t in tools:
            name = t["name"]
            exec_fn = executor_map.get(name)
            if exec_fn:
                self.register(name, t, exec_fn)

    def get(self, name: str) -> dict | None:
        return self._tools.get(name)

    def get_executor(self, name: str) -> Any | None:
        return self._executors.get(name)

    def list_definitions(self) -> list[dict]:
        return list(self._tools.values())

    def __contains__(self, name: str) -> bool:
        return name in self._tools
