"""ConcurrentToolExecutor — execute tool_calls in parallel/sequential mode."""
import asyncio
from arf.core.protocols import ToolResolver
from arf.core.results import ToolResult
from arf.sandbox.directory_boundary import DirectoryBoundary


class ConcurrentToolExecutor:
    def __init__(
        self,
        tool_resolver: ToolResolver,
        strategy: str = "parallel",
        max_concurrency: int = 5,
        tool_guard=None,
        tool_boundaries: dict[str, DirectoryBoundary] | None = None,
        default_boundary: DirectoryBoundary | None = None,
        content_guard=None,
        sandbox_manager=None,
    ) -> None:
        self._resolver = tool_resolver
        self._strategy = strategy
        self._max_concurrency = max_concurrency
        self._tool_guard = tool_guard
        self._tool_boundaries = tool_boundaries or {}
        self._default_boundary = default_boundary
        self._content_guard = content_guard
        self._sandbox_manager = sandbox_manager

    async def execute(
        self,
        tool_calls: list[dict],
        agent_mode: str = "",
        engine=None,
        state_store=None,
        workspace_dir: str = "",
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
                if workspace_dir:
                    params["_workspace"] = workspace_dir

                guard_blocked = await self._check_params(tc["name"], params)
                if guard_blocked:
                    results[tc["id"]] = guard_blocked
                    continue

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
                if workspace_dir:
                    params["_workspace"] = workspace_dir
                async with sem:
                    guard_blocked = await self._check_params(tc["name"], params)
                    if guard_blocked:
                        return tc["id"], guard_blocked
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

    async def _check_params(self, tool_name: str, params: dict) -> ToolResult | None:
        """Run path-check guard before execution. Returns ToolResult if blocked, None if safe."""
        if self._tool_guard is None:
            return None
        boundary = self._tool_boundaries.get(tool_name, self._default_boundary)
        if boundary is None:
            return None
        gr = await self._tool_guard.check(tool_name, params, boundary)
        if not gr.allowed:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"[PathCheck] {gr.reason}",
                blocked=True,
            )

        # ContentGuard: dangerous behavior check
        if self._content_guard:
            import json as _json
            params_str = _json.dumps(params, ensure_ascii=False) if params else ""
            dr = self._content_guard.check_dangerous(f"{tool_name}: {params_str}")
            if not dr.allowed:
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error=f"[ContentGuard] {dr.reason}",
                    blocked=True,
                )
        return None
