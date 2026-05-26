"""ConcurrentToolExecutor — execute tool_calls in parallel/sequential mode."""
import asyncio
from arf.core.protocols import ToolResolver
from arf.core.results import ToolResult


class ConcurrentToolExecutor:
    def __init__(self, tool_resolver: ToolResolver) -> None:
        self._resolver = tool_resolver

    async def execute(
        self,
        tool_calls: list[dict],
        strategy: str = "parallel",
        max_concurrency: int = 5,
        agent_mode: str = "",
    ) -> dict[str, ToolResult]:
        if strategy == "sequential":
            results: dict[str, ToolResult] = {}
            for tc in tool_calls:
                params = dict(tc.get("params", {}))
                if agent_mode:
                    params["_agent_mode"] = agent_mode
                results[tc["id"]] = await self._resolver.execute(
                    tc["name"], params
                )
            return results
        else:
            sem = asyncio.Semaphore(max_concurrency)
            async def _run(tc: dict):
                params = dict(tc.get("params", {}))
                if agent_mode:
                    params["_agent_mode"] = agent_mode
                async with sem:
                    return tc["id"], await self._resolver.execute(
                        tc["name"], params
                    )
            tasks = [_run(tc) for tc in tool_calls]
            resolved = await asyncio.gather(*tasks, return_exceptions=True)
            results: dict[str, ToolResult] = {}
            for item in resolved:
                if isinstance(item, Exception):
                    continue
                tid, tr = item
                results[tid] = tr
            return results
