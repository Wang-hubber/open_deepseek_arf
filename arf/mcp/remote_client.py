"""McpRemoteClient -- SSE/HTTP connection to an external MCP server."""
import os
from arf.core.config_base import McpServerConfig


def _parse_duration(s: str) -> float:
    """Parse '30s', '5m', '1h' into float seconds."""
    s = s.strip()
    if s.endswith("s"):
        return float(s[:-1])
    if s.endswith("m"):
        return float(s[:-1]) * 60
    if s.endswith("h"):
        return float(s[:-1]) * 3600
    raise ValueError(f"Unsupported duration: {s}")


class McpRemoteClient:
    """Client for a single external MCP server via SSE or HTTP transport.

    Handles connection lifecycle, JSON-RPC serialization,
    and tool name prefixing (server_name__tool_name).
    """

    def __init__(self, config: McpServerConfig) -> None:
        self.name = config.name
        self._transport = config.transport
        self._url = config.url
        self._command = config.command
        self._args = config.args
        self._api_key = os.environ.get(config.api_key_env, "") if config.api_key_env else ""
        self._timeout = _parse_duration(config.timeout)
        self._connected = False

    def _prefix_name(self, name: str) -> str:
        return f"{self.name}__{name}"

    def _strip_prefix(self, prefixed: str) -> str:
        prefix = f"{self.name}__"
        if prefixed.startswith(prefix):
            return prefixed[len(prefix):]
        return prefixed

    async def connect(self) -> None:
        """Establish connection. SSE: open stream, extract endpoint.
        HTTP: verify health. Stub for now."""
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def list_tools(self) -> list[dict]:
        """Call tools/list, prefix all tool names. Stub for now."""
        return []

    async def call_tool(self, name: str, params: dict) -> dict:
        """Call tools/call. Stub for now."""
        return {"success": False, "error": "not connected"}
