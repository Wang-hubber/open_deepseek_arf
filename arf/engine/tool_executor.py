"""ConcurrentToolExecutor — execute tool_calls in parallel/sequential mode."""
import asyncio
from pathlib import Path
from arf.core.protocols import ToolResolver
from arf.core.results import ToolResult
from arf.sandbox.directory_boundary import DirectoryBoundary

# Parameter names that are treated as filesystem paths relative to workspace.
_PATH_PARAM_NAMES = frozenset({
    "path", "file_path", "file", "output_dir", "input_dir", "cwd",
})

# Framework directory params that should NOT be resolved relative to workspace.
# These refer to infrastructure dirs (data/memory, data/state, data/traces),
# not user workspace files.
_FRAMEWORK_DIR_PARAMS = frozenset({
    "memory_dir", "state_dir", "trace_dir", "files_dir",
})


def _is_path_param(name: str) -> bool:
    """Return True if a parameter name indicates a workspace-relative path."""
    if name in _PATH_PARAM_NAMES:
        return True
    if name in _FRAMEWORK_DIR_PARAMS:
        return False
    return name.endswith(("_path", "_file", "_dir"))


def _resolve_path_params(params: dict, workspace_dir: str) -> None:
    """Resolve relative path params in-place against workspace_dir.

    Absolute paths and empty strings are left as-is (security validation
    is done separately by PathCheckToolGuard).
    """
    if not workspace_dir:
        return
    ws = Path(workspace_dir)
    for key, value in params.items():
        if not _is_path_param(key):
            continue
        if isinstance(value, str):
            if value and not value.startswith("/"):
                params[key] = str((ws / value).resolve())
        elif isinstance(value, list):
            params[key] = [
                str((ws / v).resolve()) if (isinstance(v, str) and v and not v.startswith("/")) else v
                for v in value
            ]


class ConcurrentToolExecutor:
    def __init__(
        self,
        tool_resolver: ToolResolver,
        strategy: str = "parallel",
        max_concurrency: int = 5,
        tool_guard=None,
        tool_boundaries: dict[str, DirectoryBoundary] | None = None,
        default_boundary: DirectoryBoundary | None = None,
        sandbox_manager=None,
        tool_timeout: float = 300.0,
    ) -> None:
        self._resolver = tool_resolver
        self._strategy = strategy
        self._max_concurrency = max_concurrency
        self._tool_guard = tool_guard
        self._tool_boundaries = tool_boundaries or {}
        self._default_boundary = default_boundary
        self._sandbox_manager = sandbox_manager
        self._tool_timeout = tool_timeout
        self._path_param_cache: dict[str, set[str]] = {}

    async def _get_path_param_names(self, tool_name: str) -> set[str] | None:
        """Return the set of params annotated ``format: path`` for *tool_name*.

        Returns None when the tool has no such annotations (backward
        compatible full-scan).  An empty set means the tool declares path
        params but none matched — nothing to check.
        """
        if tool_name in self._path_param_cache:
            return self._path_param_cache[tool_name]

        try:
            defs = await self._resolver.get_tool_definitions(
                query_context="", top_k=100)
        except Exception:
            return None

        for t in defs:
            name = t.name if hasattr(t, 'name') else t.get('name', '')
            params = (t.parameters if hasattr(t, 'parameters')
                      else t.get('parameters', {}))
            path_names: set[str] = set()
            for pname, prop in params.get('properties', {}).items():
                if isinstance(prop, dict) and prop.get('format') == 'path':
                    path_names.add(pname)
            self._path_param_cache[name] = path_names

        return self._path_param_cache.get(tool_name)

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

                session_id = ""
                if engine is not None:
                    session_id = getattr(engine, '_current_session_id', '')
                if session_id:
                    params["session_id"] = session_id

                guard_blocked = await self._check_params(tc["name"], params, session_id)
                if guard_blocked:
                    results[tc["id"]] = guard_blocked
                    continue

                _resolve_path_params(params, workspace_dir)
                results[tc["id"]] = await self._execute_with_timeout(
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
                session_id = ""
                if engine is not None:
                    session_id = getattr(engine, '_current_session_id', '')
                if session_id:
                    params["session_id"] = session_id
                async with sem:
                    guard_blocked = await self._check_params(tc["name"], params, session_id)
                    if guard_blocked:
                        return tc["id"], guard_blocked
                    _resolve_path_params(params, workspace_dir)
                    return tc["id"], await self._execute_with_timeout(
                        tc["name"], params
                    )
            tasks = [_run(tc) for tc in tool_calls]
            resolved = await asyncio.gather(*tasks, return_exceptions=True)
            results: dict[str, ToolResult] = {}
            for i, item in enumerate(resolved):
                if isinstance(item, Exception):
                    tc_id = tool_calls[i].get("id", f"error_{i}")
                    import logging
                    logger = logging.getLogger("arf.engine")
                    logger.error("Tool execution crashed: %s (tool=%s)", item, tool_calls[i].get("name", "?"))
                    results[tc_id] = ToolResult(
                        tool_name=tool_calls[i].get("name", ""),
                        success=False,
                        error=f"(internal) {item}",
                    )
                    continue
                tid, tr = item
                results[tid] = tr
            return results

    async def _check_params(self, tool_name: str, params: dict,
                            session_id: str = "") -> ToolResult | None:
        """Run path-check guard + content guard before execution. Returns ToolResult if blocked, None if safe."""
        # Resolve boundary: whitelist tool → allowed_dir, other → sandbox
        if tool_name in self._tool_boundaries:
            boundary = self._tool_boundaries[tool_name]
        elif self._sandbox_manager is not None and session_id:
            boundary = DirectoryBoundary(
                str(self._sandbox_manager.sandbox_path(session_id))
            )
        else:
            boundary = self._default_boundary

        # Path safety check — only scan model-supplied params, not
        # framework DI params (_workspace, _engine, _agent_mode, etc.).
        # Framework controls these values; checking them against the
        # security boundary is checking the framework against itself.
        check_params = {k: v for k, v in params.items() if not k.startswith("_")}
        if self._tool_guard is not None and boundary is not None:
            path_params = await self._get_path_param_names(tool_name)
            gr = await self._tool_guard.check(
                tool_name, check_params, boundary, path_param_names=path_params)
            if not gr.allowed:
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error=f"[PathCheck] {gr.reason}",
                    blocked=True,
                )

        return None

    async def _execute_with_timeout(
        self, name: str, params: dict,
    ) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self._resolver.execute(name, params),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                tool_name=name,
                success=False,
                error=f"Tool '{name}' timed out after {self._tool_timeout:.0f}s",
            )
