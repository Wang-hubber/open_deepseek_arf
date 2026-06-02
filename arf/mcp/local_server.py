"""ArfLocalMcpServer -- local MCP server aggregating local + remote resources."""
from pathlib import Path
from arf.resources.providers.tool_provider import ToolProvider
from arf.resources.providers.skill_provider import SkillProvider
from arf.resources.providers.plugin_provider import PluginProvider
from arf.resources.file_watcher import FileWatcher
from arf.mcp.remote_client import McpRemoteClient
from arf.core.config_base import McpServerConfig


class ArfLocalMcpServer:
    """Local MCP server that aggregates local providers + external MCP connections.

    Runs as a subprocess with stdio JSON-RPC transport.
    All tools are namespaced: arf__ for local, {server}__ for remote.
    """

    def __init__(
        self,
        tools_dir: Path,
        skills_dir: Path,
        models_dir: Path,
        plugins_dir: Path,
        plugin_names: list[str],
        remote_servers: list[McpServerConfig],
    ) -> None:
        self._tool_provider = ToolProvider(tools_dir)
        self._skill_provider = SkillProvider(skills_dir)
        # ModelProvider removed — models now resolved via config.model_defs
        self._plugin_provider = (
            PluginProvider(plugins_dir, plugin_names) if plugin_names else None
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
        """Synchronous tool listing -- uses existing ToolProvider API."""
        results: list[dict] = []
        for t in self._tool_provider.list():
            d = t.model_dump() if hasattr(t, "model_dump") else t
            d["name"] = f"arf__{d['name']}"
            results.append(d)
        if self._plugin_provider:
            for t in self._plugin_provider.list_tools():
                d = t.model_dump() if hasattr(t, "model_dump") else t
                d["name"] = f"arf__{d['name']}"
                results.append(d)
        return results

    # -- tools/call --

    async def call_tool(self, name: str, params: dict) -> dict:
        """Execute a tool by namespaced name (async, uses existing Provider APIs).

        Namespaced format: arf__tool_name or server_name__tool_name.
        """
        source, local_name = name.split("__", 1)

        if source == "arf":
            result = await self._tool_provider.execute(local_name, params)
            if result.success:
                return {"success": True, "data": result.data}
            if self._plugin_provider:
                plugin_result = await self._plugin_provider.execute(
                    local_name, params
                )
                if plugin_result and plugin_result.success:
                    return {"success": True, "data": plugin_result.data}
            return {
                "success": False,
                "error": f"Tool '{local_name}' not found: {result.error}",
            }

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
