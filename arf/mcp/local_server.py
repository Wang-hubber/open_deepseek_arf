"""ArfLocalMcpServer -- local MCP server aggregating local + remote resources."""
import asyncio
import json
import sys
from pathlib import Path
from arf.resources.providers.tool_provider import ToolProvider
from arf.resources.providers.skill_provider import SkillProvider
from arf.resources.providers.plugin_provider import PluginProvider
from arf.resources.file_watcher import FileWatcher
from arf.mcp.remote_client import McpRemoteClient
from arf.mcp.protocol import StdioFraming
from arf.core.config_base import McpServerConfig


class ArfLocalMcpServer:
    """Local MCP server that aggregates local providers + external MCP connections.

    Runs as a subprocess with stdio JSON-RPC transport.
    Tool namespace: user__{tool} for app tools, {plugin}__{tool} for plugin tools,
    {server}__{tool} for remote MCP servers.
    """

    def __init__(
        self,
        tools_dir: Path,
        skills_dir: Path,
        models_dir: Path,
        plugins_dir: Path,
        plugin_names: list[str],
        plugin_configs: dict | None = None,
        remote_servers: list[McpServerConfig] | None = None,
    ) -> None:
        self._tool_provider = ToolProvider(tools_dir)
        self._skill_provider = SkillProvider(skills_dir)
        # ModelProvider removed — models now resolved via config.model_defs
        self._plugin_provider = (
            PluginProvider(plugins_dir, plugin_names, plugin_configs)
            if plugin_names else None
        )
        self._file_watcher = FileWatcher()
        self._remote_clients: dict[str, McpRemoteClient] = {}
        for cfg in remote_servers:
            self._remote_clients[cfg.name] = McpRemoteClient(cfg)

    async def start(self) -> None:
        """Start the file watcher and connect all remote clients."""
        for client in self._remote_clients.values():
            await client.connect()

    async def stop(self) -> None:
        """Stop the file watcher and disconnect all remote clients."""
        for client in self._remote_clients.values():
            await client.disconnect()

    # -- tools/list --

    def list_tools_sync(self) -> list[dict]:
        """Synchronous tool listing with namespace prefixes.

        Namespace scheme:
          user__{tool}       — app-level tools (tools/ directory)
          {plugin}__{tool}   — plugin tools (arf/plugins/{plugin}/tools/)
          {server}__{tool}   — remote MCP servers
        """
        results: list[dict] = []
        # App tools: user__ namespace
        for t in self._tool_provider.list():
            d = t.model_dump() if hasattr(t, "model_dump") else t
            d["name"] = f"user__{d['name']}"
            results.append(d)
        # Plugin tools: {plugin}__ namespace
        if self._plugin_provider:
            for pname, t in self._plugin_provider.list_tools_with_plugin():
                d = t.model_dump() if hasattr(t, "model_dump") else t
                d["name"] = f"{pname}__{d['name']}"
                results.append(d)
        return results

    # -- tools/call --

    async def call_tool(self, name: str, params: dict) -> dict:
        """Execute a tool by namespaced name.

        Namespace scheme:
          user__{tool}       → app ToolProvider
          {plugin}__{tool}   → PluginProvider for that plugin
          {server}__{tool}   → remote MCP client
        """
        parts = name.split("__", 1)
        if len(parts) != 2:
            return {"success": False, "error": f"Tool '{name}' missing namespace prefix"}

        source, local_name = parts

        # App tools
        if source == "user":
            result = await self._tool_provider.execute(local_name, params)
            if result.success:
                return {"success": True, "data": result.data}
            return {"success": False, "error": f"Tool '{local_name}' not found"}

        # Plugin tools
        if self._plugin_provider:
            plugin_result = await self._plugin_provider.execute_plugin_tool(
                source, local_name, params
            )
            if plugin_result is not None:
                if plugin_result.success:
                    return {"success": True, "data": plugin_result.data}
                return {"success": False, "error": plugin_result.error}

        # Remote MCP servers
        client = self._remote_clients.get(source)
        if client:
            remote_result = await client.call_tool(local_name, params)
            return remote_result

        return {"success": False, "error": f"Unknown source: {source}"}

    # -- resources/list (skills) --

    def list_resources_sync(self) -> list[dict]:
        """Synchronous resource listing -- exposes skills as resources."""
        results: list[dict] = []
        for s in self._skill_provider.list():
            d = s.model_dump() if hasattr(s, "model_dump") else s
            d["uri"] = f"skills/{d['name']}.yaml"
            results.append(d)
        if self._plugin_provider:
            for s in self._plugin_provider.list_skills():
                d = s.model_dump() if hasattr(s, "model_dump") else s
                d["uri"] = f"skills/{d['name']}.yaml"
                results.append(d)
        return results


if __name__ == "__main__":
    async def _main() -> None:
        # Use buffer layer exclusively — mixing sys.stdin (TextIOWrapper)
        # with sys.stdin.buffer steals pre-read bytes between the two buffers.
        raw_line = sys.stdin.buffer.readline()
        if not raw_line:
            raise RuntimeError("No config received on stdin")
        cfg = json.loads(raw_line.decode())

        server = ArfLocalMcpServer(
            tools_dir=Path(cfg["tools_dir"]),
            skills_dir=Path(cfg["skills_dir"]),
            models_dir=Path(cfg["models_dir"]),
            plugins_dir=Path(cfg["plugins_dir"]),
            plugin_names=cfg.get("plugin_names", []),
            plugin_configs=cfg.get("plugin_configs", {}),
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
            buffer = buffer[consumed:]  # keep leftover bytes for next frame
            req = json.loads(payload)
            req_id = req.get("id", 0)
            method = req.get("method", "")
            params = req.get("params", {})

            if method == "tools/list":
                result = {"tools": server.list_tools_sync()}
            elif method == "tools/call":
                result = await server.call_tool(
                    params.get("name", ""), params.get("arguments", {}))
            elif method == "resources/list":
                result = {"resources": server.list_resources_sync()}
            else:
                result = {"error": f"Unknown method: {method}"}

            resp = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
            sys.stdout.buffer.write(StdioFraming.encode(resp))
            sys.stdout.buffer.flush()

    asyncio.run(_main())
