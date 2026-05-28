"""ConcurrentToolExecutor — execute tool_calls in parallel/sequential mode."""
import asyncio
from arf.core.protocols import ToolResolver
from arf.core.results import ToolResult


class ConcurrentToolExecutor:
    def __init__(
        self,
        tool_resolver: ToolResolver,
        strategy: str = "parallel",
        max_concurrency: int = 5,
    ) -> None:
        self._resolver = tool_resolver
        self._strategy = strategy
        self._max_concurrency = max_concurrency

    async def execute(
        self,
        tool_calls: list[dict],
        agent_mode: str = "",
        engine=None,
        state_store=None,
    ) -> dict[str, ToolResult]:
        strategy = self._strategy
        max_concurrency = self._max_concurrency
        if strategy == "sequential":
            results: dict[str, ToolResult] = {}
            for tc in tool_calls:
                params = dict(tc.get("params", {}))
                if agent_mode:
                    params["_agent_mode"] = agent_mode
                if engine is not None:
                    params["_engine"] = engine
                if state_store is not None:
                    params["_state_store"] = state_store
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
                if engine is not None:
                    params["_engine"] = engine
                if state_store is not None:
                    params["_state_store"] = state_store
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
