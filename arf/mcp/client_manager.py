"""McpClientManager — Agent-side entry point for MCP resource management.

Routes local tools (user__ and {plugin}__ namespaces) in-process via
ToolProvider / PluginProvider.  Only remote MCP servers (server__ namespace)
go through a stdio subprocess for isolation — and only when at least one
remote server is configured.

Implements the ToolResolver protocol so it can drop into
ConcurrentToolExecutor and GraphEngine without interface changes.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

from arf.mcp.protocol import StdioFraming, JsonRpcRequest
from arf.mcp.remote_client import McpRemoteClient
from arf.core.config_base import McpServerConfig
from arf.core.results import ToolResult
from arf.resources.providers.tool_provider import ToolProvider
from arf.resources.providers.skill_provider import SkillProvider
from arf.resources.providers.plugin_provider import PluginProvider

logger = logging.getLogger("arf.mcp")


def _filter_serializable(params: dict, *, keep_di: bool = False) -> dict:
    """Keep only JSON-serializable values.

    When *keep_di* is True, params starting with ``_`` (DI-injected objects
    like ``_engine``, ``_state_store``) are preserved for in-process execution.
    Remote (subprocess) callers must set keep_di=False.
    """
    clean: dict = {}
    for k, v in params.items():
        if keep_di and k.startswith("_"):
            clean[k] = v
            continue
        try:
            json.dumps(v)
            clean[k] = v
        except (TypeError, ValueError):
            pass
    return clean


def _tc_to_dict(tc) -> dict:
    """ToolConfig → dict, preserving the original name (namespace added by caller)."""
    if hasattr(tc, "model_dump"):
        return tc.model_dump()
    if isinstance(tc, dict):
        return dict(tc)
    return {"name": getattr(tc, "name", ""),
            "description": getattr(tc, "description", ""),
            "parameters": getattr(tc, "parameters", {})}


class McpClientManager:
    """Unified tool / skill resolver.

    Local tools (ToolProvider + PluginProvider) are resolved **in-process**
    — zero serialization overhead, zero pipe-deadlock risk.  Only remote
    MCP servers use a subprocess for isolation, and only when at least one
    ``mcp_servers`` entry is configured.
    """

    def __init__(
        self,
        tools_dir: Path,
        skills_dir: Path,
        models_dir: Path,
        plugins_dir: Path,
        mcp_servers: list[McpServerConfig],
        plugin_names: list[str],
        plugin_configs: dict | None = None,
    ) -> None:
        self._tools_dir = tools_dir
        self._skills_dir = skills_dir
        self._plugins_dir = plugins_dir
        self._mcp_servers = mcp_servers
        self._plugin_names = plugin_names
        self._plugin_configs = plugin_configs or {}

        # ---- kernel tools (built-in, no file-based provider) ----
        self._kernel_tools: dict[str, object] = {}

        # ---- local providers (in-process, always available) ----
        self._tool_provider = ToolProvider(tools_dir)
        self._skill_provider = SkillProvider(skills_dir)
        self._plugin_provider: PluginProvider | None = None
        if plugin_names:
            self._plugin_provider = PluginProvider(
                plugins_dir, plugin_names, plugin_configs)

        # ---- remote MCP (subprocess, only when mcp_servers is non-empty) ----
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._remote_started: bool = False
        self._remote_healthy: bool = False
        self._remote_request_id: int = 0
        self._remote_lock: asyncio.Lock = asyncio.Lock()

        self._started = False

    def register_kernel_tool(self, name: str, execute_fn) -> None:
        """Register a built-in kernel tool (namespace: kernel__)."""
        self._kernel_tools[name] = execute_fn

    # ==================================================================
    # Lifecycle
    # ==================================================================

    async def start(self) -> None:
        """Start remote MCP connections (if any).  Local providers need no start."""
        if self._started:
            return
        self._started = True

        if not self._mcp_servers:
            return  # nothing remote to start

        await self._remote_start()

    async def _remote_start(self) -> None:
        """Spawn the local-server subprocess for remote MCP servers."""
        if self._remote_started:
            return

        config_json = json.dumps({
            # tools / skills / plugins are NOT forwarded — the subprocess
            # only handles remote servers now
            "remote_servers": [s.model_dump() for s in self._mcp_servers],
        })

        self._process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "arf.mcp.local_server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._stderr_task = asyncio.create_task(self._drain_stderr())

        try:
            if self._process.stdin:
                self._process.stdin.write(config_json.encode() + b"\n")
                await self._process.stdin.drain()
                self._remote_healthy = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            self._remote_healthy = False
        self._remote_started = True

    async def _drain_stderr(self) -> None:
        """Read stderr lines from the subprocess and log them."""
        _log = logging.getLogger("arf.mcp.local_server")
        try:
            while self._process and self._process.stderr:
                line = await self._process.stderr.readline()
                if not line:
                    break
                _log.debug("%s", line.decode(errors="replace").rstrip())
        except Exception:
            pass

    async def stop(self) -> None:
        """Terminate the remote subprocess and clean up."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._process.kill()
                    await asyncio.wait_for(self._process.wait(), timeout=2.0)
                except (ProcessLookupError, asyncio.TimeoutError):
                    pass
            self._process = None
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None
        self._remote_started = False
        self._started = False

    @property
    def healthy(self) -> bool:
        # Local providers are always healthy (no subprocess to hang).
        # Remote health only matters when remote servers are configured.
        if not self._mcp_servers:
            return True
        return self._remote_healthy

    # ==================================================================
    # Tool listing (in-process for local, subprocess for remote)
    # ==================================================================

    def _list_local_tools(self) -> list[dict]:
        """List local tools (user__ + plugin__ namespaces) — synchronous."""
        results: list[dict] = []

        # App tools: user__ namespace
        for t in self._tool_provider.list():
            d = _tc_to_dict(t)
            d["name"] = f"user__{d['name']}"
            results.append(d)

        # Plugin tools: {plugin}__ namespace
        if self._plugin_provider:
            for pname, t in self._plugin_provider.list_tools_with_plugin():
                d = _tc_to_dict(t)
                d["name"] = f"{pname}__{d['name']}"
                results.append(d)

        return results

    def _list_local_resources(self) -> list[dict]:
        """List local resources (skills) — synchronous."""
        results: list[dict] = []
        for s in self._skill_provider.list():
            d = _tc_to_dict(s)
            d["uri"] = f"skills/{d.get('name', '')}.yaml"
            results.append(d)
        return results

    async def _list_remote_tools(self) -> list[dict]:
        """List tools from the remote subprocess."""
        result = await self._remote_send("tools/list", {})
        return result.get("tools", [])

    # ==================================================================
    # ToolResolver protocol
    # ==================================================================

    async def get_tool_definitions(
        self, query_context: str = "", top_k: int = 10,
    ) -> list[dict]:
        """Get all tools as plain dicts.  Local tools are resolved in-process.

        Filters out tools whose parameter names conflict with framework-injected
        params (e.g. session_id, _workspace).
        """
        tools_data: list[dict] = list(self._list_local_tools())

        if self._remote_started:
            try:
                remote_tools = await self._list_remote_tools()
                tools_data.extend(remote_tools)
            except Exception:
                logger.debug("Failed to list remote tools", exc_info=True)

        # Filter tools with reserved param names (framework-injected params)
        from arf.resources.providers.tool_provider import _RESERVED_PARAM_NAMES
        clean: list[dict] = []
        for t in tools_data:
            params = t.get("parameters", {}).get("properties", {})
            conflicts = {p for p in params if p.startswith("_")} | (set(params) & _RESERVED_PARAM_NAMES)
            if conflicts:
                logger.warning(
                    "Skipping tool '%s': params %s conflict with framework-injected params",
                    t.get("name", "?"), sorted(conflicts),
                )
                continue
            clean.append(t)
        return clean

    async def execute(self, tool_name: str, params: dict) -> ToolResult:
        """Execute a tool.  Local tools run in-process."""
        from arf.core.tool_naming import split_name

        source, local_name = split_name(tool_name)
        if not source:
            return ToolResult(
                tool_name=tool_name, success=False,
                error=f"Tool '{tool_name}' missing namespace prefix")

        # ---- kernel: kernel__ (built-in, no file-based provider) ----
        if source == "kernel":
            fn = self._kernel_tools.get(local_name)
            if fn is not None:
                try:
                    result = await fn(**_filter_serializable(params, keep_di=True))
                    if isinstance(result, dict) and "ok" in result:
                        return ToolResult(
                            tool_name=tool_name, success=result["ok"],
                            data=result, error=result.get("error"))
                    return ToolResult(
                        tool_name=tool_name, success=True, data=result)
                except Exception as e:
                    return ToolResult(
                        tool_name=tool_name, success=False, error=str(e))
            return ToolResult(
                tool_name=tool_name, success=False,
                error=f"Kernel tool '{local_name}' not registered")

        # ---- local: user__ (keep DI params for in-process execution) ----
        if source == "user":
            return await self._tool_provider.execute(
                local_name, _filter_serializable(params, keep_di=True))

        # ---- local: {plugin}__ (keep DI params for in-process execution) ----
        if self._plugin_provider:
            result = await self._plugin_provider.execute_plugin_tool(
                source, local_name, _filter_serializable(params, keep_di=True))
            if result is not None:
                return result

        # ---- remote: {server}__ (strip DI objects — not serializable) ----
        clean_params = _filter_serializable(params, keep_di=False)
        if self._remote_started:
            try:
                remote_result = await self._remote_send("tools/call", {
                    "name": tool_name,
                    "arguments": clean_params,
                })
                return ToolResult(
                    tool_name=tool_name,
                    success=remote_result.get("success", False),
                    data=remote_result.get("data", {}),
                    error=remote_result.get("error"),
                )
            except Exception as e:
                return ToolResult(
                    tool_name=tool_name, success=False, error=str(e))

        return ToolResult(
            tool_name=tool_name, success=False,
            error=f"Unknown source: {source}")

    # ---- Sync wrapper for startup ----

    def get_tool_definitions_sync(self) -> list[dict]:
        """Synchronous tool listing — local providers only (no subprocess)."""
        return self._list_local_tools()

    # ==================================================================
    # Remote subprocess communication (only when mcp_servers configured)
    # ==================================================================

    @staticmethod
    def _consume_frame(buffer: bytes) -> tuple[str | None, bytes]:
        """Decode first complete frame in *buffer*."""
        decoded = StdioFraming.decode(buffer)
        if decoded is None:
            return None, buffer

        header_end = buffer.find(b"\r\n\r\n")
        if header_end == -1:
            return None, buffer
        header = buffer[:header_end].decode("ascii")
        if not header.startswith("Content-Length: "):
            return None, buffer
        try:
            content_length = int(header[len("Content-Length: "):])
        except ValueError:
            return None, buffer
        consumed = header_end + 4 + content_length
        return decoded, buffer[consumed:]

    async def _remote_send(self, method: str, params: dict,
                           timeout: float = 15.0) -> dict:
        """Send a JSON-RPC request to the remote subprocess."""
        async def _do_request() -> dict:
            self._remote_request_id += 1
            sent_id = self._remote_request_id
            req = JsonRpcRequest(id=sent_id, method=method, params=params)
            payload = req.model_dump_json(by_alias=True)
            framed = StdioFraming.encode(payload)

            async with self._remote_lock:
                if not self._remote_healthy:
                    return {}

                if self._process and self._process.stdin:
                    try:
                        self._process.stdin.write(framed)
                        await self._process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        self._remote_healthy = False
                        return {}

                if not (self._process and self._process.stdout):
                    return {}

                buf = b""
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            self._process.stdout.read(4096), timeout=10.0)
                    except (asyncio.TimeoutError, ConnectionResetError,
                            BrokenPipeError, OSError):
                        self._remote_healthy = False
                        return {}

                    if not chunk:
                        return {}
                    buf += chunk

                    while True:
                        decoded, buf = self._consume_frame(buf)
                        if decoded is None:
                            break

                        resp = json.loads(decoded)
                        resp_id = resp.get("id")
                        if resp_id != sent_id:
                            logger.debug(
                                "Discarding stale response id=%s, expected=%s",
                                resp_id, sent_id)
                            continue

                        if "result" in resp:
                            return resp["result"]
                        if "error" in resp:
                            return {"error": resp["error"].get("message", "MCP error")}
                        break

                return {}

        try:
            return await asyncio.wait_for(_do_request(), timeout=timeout)
        except asyncio.TimeoutError:
            self._remote_healthy = False
            return {}
