"""ResourceResolver — unified resource resolution with override merge.

Resolves tools, skills, and models from filesystem providers.
Merges agent.yaml overrides on top of filesystem definitions.
Override priority: agent.yaml field > filesystem field > Pydantic default.

Backward-compat: DefaultToolResolver preserved for existing callers
(base.py, etc.) with the old constructor signature, delegating
internally to ResourceResolver.
"""
from arf.core.protocols.resources import ToolDefinition, ToolProvider, ToolRetriever, ToolBackend
from arf.core.config_base import ToolConfig, SkillConfig, ModelConfig
from arf.core.results import ToolResult


class ResourceResolver:
    """Unified resource resolver — tools, skills, models, override merge,
    dynamic reload, and config generation."""

    def __init__(
        self,
        tool_provider,
        skill_provider=None,
        agent_yaml_overrides: dict | None = None,
    ):
        self._tool_provider = tool_provider
        self._skill_provider = skill_provider
        self._overrides = agent_yaml_overrides or {}
        self._plugin_provider = None

    def set_plugin_provider(self, plugin_provider) -> None:
        """Register a PluginProvider to merge its tools/skills into resolution."""
        self._plugin_provider = plugin_provider

    # -- tools (backward-compat with DefaultToolResolver) --

    async def get_tool_definitions(
        self, query_context: str = "", top_k: int = 10,
    ) -> list[ToolDefinition]:
        tools = list(await self._tool_provider.list_tools())
        if self._plugin_provider:
            tools.extend(self._plugin_provider.list_tools())
        overrides = self._overrides.get("tools", [])
        merged = self._merge_configs(tools, overrides, ToolConfig)
        return [
            ToolDefinition(name=t.name, description=t.description, parameters=t.parameters)
            for t in merged
        ]

    def get_tool_definitions_sync(self) -> list[ToolConfig]:
        """Synchronous wrapper — returns tool defs merged with agent.yaml overrides.

        Merges filesystem + plugin tool definitions (with full descriptions from
        tool.yaml) with agent.yaml overrides. Used by BaseAgent to feed descriptions
        back into config.tools before system prompt assembly.
        """
        tools = list(self._tool_provider.list_kernel()) + list(self._tool_provider.list_dynamic())
        if self._plugin_provider:
            tools.extend(self._plugin_provider.list_tools())
        overrides = self._overrides.get("tools", [])
        return self._merge_configs(tools, overrides, ToolConfig)

    def get_skill_definitions_sync(self) -> list[SkillConfig]:
        """Synchronous wrapper — returns skill defs merged with agent.yaml overrides."""
        skills = list(self._skill_provider.list()) if self._skill_provider else []
        if self._plugin_provider:
            skills.extend(self._plugin_provider.list_skills())
        overrides = self._overrides.get("skills", [])
        return self._merge_configs(skills, overrides, SkillConfig)

    async def execute(self, tool_name: str, params: dict) -> ToolResult:
        result = await self._tool_provider.execute(tool_name, params)
        if not result.success and self._plugin_provider:
            plugin_result = await self._plugin_provider.execute(tool_name, params)
            if plugin_result is not None:
                return plugin_result
        return result

    # -- skills --

    def get_skill_definitions(self) -> list[SkillConfig]:
        if self._skill_provider is None:
            return []
        skills = list(self._skill_provider.list())
        if self._plugin_provider:
            skills.extend(self._plugin_provider.list_skills())
        overrides = self._overrides.get("skills", [])
        return self._merge_configs(skills, overrides, SkillConfig)

    # -- cache --

    async def reload_dynamic(self) -> None:
        """Clear dynamic caches across all providers."""
        if hasattr(self._tool_provider, "invalidate_dynamic"):
            self._tool_provider.invalidate_dynamic()
        if self._skill_provider and hasattr(self._skill_provider, "invalidate_dynamic"):
            self._skill_provider.invalidate_dynamic()

    # -- override merge --

    def _merge_configs(
        self, fs_items: list, override_list: list[dict], config_cls,
        key_field: str = "name",
    ) -> list:
        """Merge filesystem items with agent.yaml overrides.

        Filesystem is base. Override dicts with matching key_field are applied on top.
        Empty strings / empty dicts are treated as "not set" and do NOT override
        filesystem values. Override-only entries (not in filesystem) are appended.
        """
        # Clean overrides: remove empty defaults so filesystem values are preserved
        cleaned = []
        for o in override_list:
            c = {k: v for k, v in o.items()
                 if v not in ("", {}, [], None)}
            if c:
                cleaned.append(c)
        override_map = {o[key_field]: o for o in cleaned if key_field in o}
        result = []
        seen = set()
        for item in fs_items:
            item_key = getattr(item, key_field)
            if item_key in override_map:
                merged = item.model_copy(update=override_map[item_key])
                seen.add(item_key)
            else:
                merged = item
            result.append(merged)
        # Append overrides without filesystem counterpart
        for key, ov in override_map.items():
            if key not in seen:
                result.append(config_cls(**ov))
        return result

    # -- config generation --

    async def generate_config(self) -> dict:
        """Dump all discovered resources as agent.yaml-compatible dict."""
        config = {}
        if self._tool_provider:
            tools = await self._tool_provider.list_tools()
            config["tools"] = [t.model_dump(exclude_none=True) for t in tools]
        if self._skill_provider:
            config["skills"] = [s.model_dump(exclude_none=True) for s in self._skill_provider.list()]
        return config


class DefaultToolResolver:
    """Backward-compatible wrapper — preserves old constructor signature.

    Old callers that construct ``DefaultToolResolver(providers=[...])``
    continue to work unchanged.  Delegates to ``ResourceResolver``
    internally for the new unified API.
    """

    def __init__(
        self,
        providers: list,
        retriever: ToolRetriever | None = None,
        backend: ToolBackend | None = None,
    ) -> None:
        # Old code passed a list of ToolProviders; in practice always one.
        tool_provider = providers[0] if providers else None
        self._inner = ResourceResolver(tool_provider=tool_provider)

    async def get_tool_definitions(
        self, query_context: str = "", top_k: int = 10,
    ) -> list[ToolDefinition]:
        return await self._inner.get_tool_definitions(query_context, top_k)

    async def execute(self, tool_name: str, params: dict) -> ToolResult:
        return await self._inner.execute(tool_name, params)

    async def reload(self) -> None:
        """Reload all providers — clears cached tool lists for re-scan."""
        await self._inner.reload_dynamic()
