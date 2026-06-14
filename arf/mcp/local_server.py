"""ArfLocalMcpServer -- lightweight subprocess proxy for remote MCP servers.

Local tools (user__ and {plugin}__ namespaces) are now handled in-process
by McpClientManager.  This subprocess is only spawned when at least one
remote ``mcp_servers`` entry is configured, providing process isolation
for external MCP connections.
"""
import asyncio
import json
import sys
from pathlib import Path

from arf.mcp.remote_client import McpRemoteClient
from arf.mcp.protocol import StdioFraming
from arf.core.config_base import McpServerConfig


class ArfLocalMcpServer:
    """Subprocess proxy for remote MCP servers only."""

    def __init__(
        self,
        remote_servers: list[McpServerConfig] | None = None,
    ) -> None:
        self._remote_clients: dict[str, McpRemoteClient] = {}
        for cfg in (remote_servers or []):
            self._remote_clients[cfg.name] = McpRemoteClient(cfg)

    async def start(self) -> None:
        for client in self._remote_clients.values():
            await client.connect()

    async def stop(self) -> None:
        for client in self._remote_clients.values():
            await client.disconnect()

    # -- tools/list (remote only) --

    def list_tools_sync(self) -> list[dict]:
        """Synchronous listing of remote MCP tools."""
        results: list[dict] = []
        for server_name, client in self._remote_clients.items():
            # list_tools() is async but we're sync — spawn a one-shot loop
            try:
                tools = asyncio.run(client.list_tools())
            except Exception:
                continue
            for t in tools:
                d = dict(t) if isinstance(t, dict) else (
                    t.model_dump() if hasattr(t, "model_dump") else {"name": str(t)})
                d["name"] = f"{server_name}__{d.get('name', '')}"
                results.append(d)
        return results

    # -- tools/call (remote only) --

    async def call_tool(self, name: str, params: dict) -> dict:
        parts = name.split("__", 1)
        if len(parts) != 2:
            return {"success": False, "error": f"Tool '{name}' missing namespace prefix"}

        source, local_name = parts
        client = self._remote_clients.get(source)
        if client:
            return await client.call_tool(local_name, params)
        return {"success": False, "error": f"Unknown remote server: {source}"}


if __name__ == "__main__":
    async def _main() -> None:
        raw_line = sys.stdin.buffer.readline()
        if not raw_line:
            raise RuntimeError("No config received on stdin")
        cfg = json.loads(raw_line.decode())

        server = ArfLocalMcpServer(
            remote_servers=[McpServerConfig(**s) for s in cfg.get("remote_servers", [])],
        )
        await server.start()

        loop = asyncio.get_event_loop()
        buffer = b""
        while True:
            chunk = await loop.run_in_executor(None, sys.stdin.buffer.read1, 4096)
            if not chunk:
                break
            buffer += chunk
            payload = StdioFraming.decode(buffer)
            if not payload:
                continue
            consumed = buffer.find(payload.encode()) + len(payload.encode())
            buffer = buffer[consumed:]
            req = json.loads(payload)
            req_id = req.get("id", 0)
            method = req.get("method", "")
            params = req.get("params", {})

            if method == "tools/list":
                result = {"tools": server.list_tools_sync()}
            elif method == "tools/call":
                result = await server.call_tool(
                    params.get("name", ""), params.get("arguments", {}))
            else:
                result = {"error": f"Unknown method: {method}"}

            resp = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
            sys.stdout.buffer.write(StdioFraming.encode(resp))
            sys.stdout.buffer.flush()

    asyncio.run(_main())
