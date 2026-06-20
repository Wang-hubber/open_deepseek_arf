"""ToolExecutor — execute tool calls. Minimal, no validation."""
from __future__ import annotations
import asyncio
import time
import logging
from typing import Any

from arf.tooling.registry import ToolRegistry

logger = logging.getLogger("arf.tooling")


class ToolResult:
    def __init__(self, success: bool, data: Any = None, error: str = "", duration_ms: float = 0):
        self.success = success
        self.data = data
        self.error = error
        self.duration_ms = duration_ms


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, timeout: float = 60.0) -> None:
        self._registry = registry
        self._timeout = timeout

    async def execute(self, tool_calls: list[dict]) -> dict[str, ToolResult]:
        tasks = []
        for tc in tool_calls:
            tasks.append(self._execute_one(tc))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: dict[str, ToolResult] = {}
        for tc, r in zip(tool_calls, results):
            if isinstance(r, Exception):
                out[tc["id"]] = ToolResult(success=False, error=str(r))
            else:
                out[tc["id"]] = r
        return out

    async def _execute_one(self, tc: dict) -> ToolResult:
        name = tc.get("name", "")
        params = tc.get("params", {})
        executor = self._registry.get_executor(name)
        if executor is None:
            return ToolResult(success=False, error=f"Tool not found: {name}")

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                executor(**params) if callable(executor) else executor.execute(params),
                timeout=self._timeout,
            )
            elapsed = (time.monotonic() - start) * 1000
            if isinstance(result, dict):
                return ToolResult(success=True, data=result, duration_ms=elapsed)
            return ToolResult(success=True, data=str(result), duration_ms=elapsed)
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Tool '{name}' timed out after {self._timeout}s")
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(success=False, error=str(exc), duration_ms=elapsed)
