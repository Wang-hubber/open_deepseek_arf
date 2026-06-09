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


def _filter_serializable(params: dict) -> dict:
    """Keep only JSON-serializable values; strip DI objects like _engine, _state_store."""
    clean: dict = {}
    for k, v in params.items():
        try:
            json.dumps(v)
            clean[k] = v
        except (TypeError, ValueError):
            pass
    return clean


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
        plugin_configs: dict | None = None,
    ) -> None:
        self._tools_dir = tools_dir
        self._skills_dir = skills_dir
        self._models_dir = models_dir
        self._plugins_dir = plugins_dir
        self._mcp_servers = mcp_servers
        self._plugin_names = plugin_names
        self._plugin_configs = plugin_configs or {}
        self._process: asyncio.subprocess.Process | None = None
        self._started = False
        self._healthy = False
        self._request_id = 0
        self._send_lock = asyncio.Lock()

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
            "plugin_configs": self._plugin_configs,
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

    @staticmethod
    def _consume_frame(buffer: bytes) -> tuple[str | None, bytes]:
        """Decode first complete frame in *buffer*, returning
        ``(payload, remaining_bytes)``.  Returns ``(None, buffer)`` when
        no complete frame is available yet.
        """
        decoded = StdioFraming.decode(buffer)
        if decoded is None:
            return None, buffer

        # Re-parse the header so we know how many bytes to consume.
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

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and return the result dict.

        Serialized via ``_send_lock`` so only one request is in-flight at a
        time.  Response id is verified against the sent request id; mismatched
        (stale) responses are discarded and the correct one is waited for.
        """
        self._request_id += 1
        sent_id = self._request_id
        req = JsonRpcRequest(id=sent_id, method=method, params=params)
        payload = req.model_dump_json(by_alias=True)
        framed = StdioFraming.encode(payload)

        async with self._send_lock:
            if not self._healthy:
                return {}

            if self._process and self._process.stdin:
                try:
                    self._process.stdin.write(framed)
                    await self._process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    self._healthy = False
                    return {}

            if not (self._process and self._process.stdout):
                return {}

            buf = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self._process.stdout.read(4096), timeout=10.0
                    )
                except (asyncio.TimeoutError, ConnectionResetError,
                        BrokenPipeError, OSError):
                    self._healthy = False
                    return {}

                if not chunk:
                    return {}
                buf += chunk

                while True:
                    decoded, buf = self._consume_frame(buf)
                    if decoded is None:
                        break  # need more data

                    resp = json.loads(decoded)
                    resp_id = resp.get("id")

                    if resp_id != sent_id:
                        logger = __import__('logging').getLogger("arf.mcp")
                        logger.debug("Discarding stale response id=%s, expected=%s",
                                     resp_id, sent_id)
                        continue  # discard, try next frame in buf

                    if "result" in resp:
                        return resp["result"]
                    if "error" in resp:
                        return {"error": resp["error"].get("message", "MCP error")}
                    # response with neither result nor error — malformed, discard
                    break

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
            # Filter non-serializable values (DI objects like _engine, _state_store)
            # rather than hardcoding a blacklist — serializable params pass through.
            clean_params = _filter_serializable(params)
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
