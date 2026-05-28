"""PluginProvider — scan arf/plugins/{name}/ for tools/ and skills/."""
from pathlib import Path

from arf.resources.providers.tool_provider import ToolProvider
from arf.resources.providers.skill_provider import SkillProvider
from arf.core.config_base import ToolConfig, SkillConfig
from arf.core.results import ToolResult


class PluginProvider:
    """Scans plugin directories for tools and skills.

    Each plugin is a subdirectory under plugins_dir with:
      tools/   — tool.yaml + function.py pairs (same structure as app tools)
      skills/  — *.yaml skill definitions (same structure as app skills)
    """

    def __init__(self, plugins_dir: str | Path, enabled: list[str] | None = None):
        self._root = Path(plugins_dir)
        self._enabled = set(enabled or [])
        self._tool_providers: dict[str, ToolProvider] = {}
        self._skill_providers: dict[str, SkillProvider] = {}
        self._scanned_tools: list[ToolConfig] = []
        self._scanned_skills: list[SkillConfig] = []
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        self._tool_providers.clear()
        self._skill_providers.clear()
        self._scanned_tools.clear()
        self._scanned_skills.clear()

        if not self._root.exists():
            return

        for plugin_dir in sorted(self._root.iterdir()):
            if not plugin_dir.is_dir():
                continue
            if plugin_dir.name not in self._enabled:
                continue

            tools_dir = plugin_dir / "tools"
            if tools_dir.exists() and tools_dir.is_dir():
                tp = ToolProvider(str(tools_dir))
                tp._load()
                self._tool_providers[plugin_dir.name] = tp

            skills_dir = plugin_dir / "skills"
            if skills_dir.exists() and skills_dir.is_dir():
                sp = SkillProvider(str(skills_dir))
                sp._load()
                self._skill_providers[plugin_dir.name] = sp

        # Collect tools from all enabled plugin tool providers
        for tp in self._tool_providers.values():
            self._scanned_tools.extend(tp.list_kernel())
            self._scanned_tools.extend(tp.list_dynamic())

        # Collect skills from all enabled plugin skill providers
        for sp in self._skill_providers.values():
            self._scanned_skills.extend(sp.list_kernel())
            self._scanned_skills.extend(sp.list_dynamic())

    def list_tools(self) -> list[ToolConfig]:
        if not self._loaded:
            self._load()
        return list(self._scanned_tools)

    def list_skills(self) -> list[SkillConfig]:
        if not self._loaded:
            self._load()
        return list(self._scanned_skills)

    async def execute(self, name: str, params: dict) -> ToolResult | None:
        """Try to execute a plugin tool. Returns None if not found."""
        if not self._loaded:
            self._load()
        for tp in self._tool_providers.values():
            cfg = await tp.resolve(name)
            if cfg is not None:
                return await tp.execute(name, params)
        return None
