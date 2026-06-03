"""McpClientManager -- Agent-side entry point for MCP resource management.

Manages a local MCP server subprocess via stdio JSON-RPC, implementing the
ToolResolver protocol so it can drop into ConcurrentToolExecutor and
GraphEngine without interface changes.
"""
import asyncio
import json
import sys
from pathlib import Path

from arf.mcp.protocol import StdioFraming, JsonRpcRequest
from arf.core.config_base import McpServerConfig
from arf.core.protocols.resources import ToolDefinition, ToolResolver
from arf.core.results import ToolResult


class McpClientManager:
    """Manages a local MCP server subprocess via stdio JSON-RPC.

    Implements the ToolResolver protocol so it can drop into
    ConcurrentToolExecutor and GraphEngine without interface changes.
    """

    def __init__(
        self,
        tools_dir: Path,
        skills_dir: Path,
        models_dir: Path,
        plugins_dir: Path,
        mcp_servers: list[McpServerConfig],
        plugin_names: list[str],
    ) -> None:
        self._tools_dir = tools_dir
        self._skills_dir = skills_dir
        self._models_dir = models_dir
        self._plugins_dir = plugins_dir
        self._mcp_servers = mcp_servers
        self._plugin_names = plugin_names
        self._process: asyncio.subprocess.Process | None = None
        self._started = False
        self._healthy = False
        self._request_id = 0

    async def start(self) -> None:
        """Spawn the local MCP server subprocess and establish stdio connection."""
        if self._started:
            return

        config_json = json.dumps({
            "tools_dir": str(self._tools_dir),
            "skills_dir": str(self._skills_dir),
            "models_dir": str(self._models_dir),
            "plugins_dir": str(self._plugins_dir),
            "plugin_names": self._plugin_names,
            "remote_servers": [s.model_dump() for s in self._mcp_servers],
        })

        self._process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "arf.mcp.local_server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            if self._process.stdin:
                self._process.stdin.write(config_json.encode() + b"\n")
                await self._process.stdin.drain()
                self._healthy = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            self._healthy = False
        self._started = True

    async def stop(self) -> None:
        """Terminate the subprocess and clean up."""
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
        self._started = False

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and return the result dict."""
        self._request_id += 1
        req = JsonRpcRequest(id=self._request_id, method=method, params=params)
        payload = req.model_dump_json(by_alias=True)
        framed = StdioFraming.encode(payload)

        if not self._healthy:
            return {}

        if self._process and self._process.stdin:
            try:
                self._process.stdin.write(framed)
                await self._process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                self._healthy = False
                return {}

        if self._process and self._process.stdout:
            buffer = b""
            try:
                while True:
                    chunk = await asyncio.wait_for(
                        self._process.stdout.read(4096), timeout=10.0
                    )
                    if not chunk:
                        break
                    buffer += chunk
                    decoded = StdioFraming.decode(buffer)
                    if decoded:
                        resp = json.loads(decoded)
                        if "result" in resp:
                            return resp["result"]
                        if "error" in resp:
                            return {"error": resp["error"].get("message", "MCP error")}
                        break
            except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError, OSError):
                self._healthy = False

        return {}

    @property
    def healthy(self) -> bool:
        return self._healthy

    # -- ToolResolver protocol --

    async def get_tool_definitions(
        self, query_context: str = "", top_k: int = 10,
    ) -> list[ToolDefinition]:
        """Get all tools (MCP tools/list). Implements ToolResolver protocol."""
        try:
            result = await self._send_request("tools/list", {})
            tools_data = result.get("tools", [])
            return [
                ToolDefinition(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    parameters=t.get("parameters", {}),
                )
                for t in tools_data
            ]
        except Exception:
            return []

    async def execute(self, tool_name: str, params: dict) -> ToolResult:
        """Execute a tool (MCP tools/call). Implements ToolResolver protocol."""
        try:
            # _-prefixed params (_engine, _state_store, _workspace, etc.) are
            # framework DI — ConcurrentToolExecutor injects them so local tools
            # can access engine services without globals. They are not
            # JSON-serializable and meaningless across a process boundary:
            # the subprocess has no access to the parent's ControlPlane or
            # StateStore. Tools that depend on these (undo, planner, subagent)
            # must run in-process; pure tools work fine with them stripped.
            clean_params = {k: v for k, v in params.items() if not k.startswith("_")}
            result = await self._send_request("tools/call", {
                "name": tool_name,
                "arguments": clean_params,
            })
            return ToolResult(
                tool_name=tool_name,
                success=result.get("success", False),
                data=result.get("data", {}),
                error=result.get("error"),
            )
        except Exception as e:
            return ToolResult(tool_name=tool_name, success=False, error=str(e))

    # -- Sync wrappers for startup --

    def get_tool_definitions_sync(self) -> list[ToolDefinition]:
        """Sync wrapper — safe to call from any context including async."""
        from threading import Thread
        result: list[ToolDefinition] = []
        def _run() -> None:
            nonlocal result
            result = asyncio.run(self.get_tool_definitions())
        t = Thread(target=_run)
        t.start()
        t.join()
        return result
