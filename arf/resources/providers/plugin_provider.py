"""PluginProvider — scan arf/plugins/{name}/ for tools, skills, hooks, and plugin classes."""
import importlib
import json
import logging
import sys as _sys
from pathlib import Path

import yaml

from arf.resources.providers.tool_provider import ToolProvider
from arf.resources.providers.skill_provider import SkillProvider
from arf.core.config_base import ToolConfig, SkillConfig, HookDefinition
from arf.core.results import ToolResult

logger = logging.getLogger("arf.plugins.discovery")


class PluginProvider:
    """Scans plugin directories for tools, skills, hooks, and plugin classes.

    Each plugin is a subdirectory under plugins_dir with:
      tools/      — tool.yaml + function.py pairs
      skills/     — *.yaml skill definitions
      hooks/      — *.py hook scripts (subprocess)
      plugin.yaml — plugin metadata + config
      plugin.py   — plugin class (in-process, auto-discovered)
    """

    def __init__(self, plugins_dir: str | Path, enabled: list[str] | None = None):
        self._root = Path(plugins_dir)
        self._enabled = set(enabled or [])
        self._tool_providers: dict[str, ToolProvider] = {}
        self._skill_providers: dict[str, SkillProvider] = {}
        self._scanned_tools: list[ToolConfig] = []
        self._scanned_skills: list[SkillConfig] = []
        self._scanned_hooks: list[HookDefinition] = []
        self._scanned_plugins: list = []
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        self._tool_providers.clear()
        self._skill_providers.clear()
        self._scanned_tools.clear()
        self._scanned_skills.clear()
        self._scanned_hooks.clear()
        self._scanned_plugins.clear()

        if not self._root.exists():
            return

        for plugin_dir in sorted(self._root.iterdir()):
            if not plugin_dir.is_dir():
                continue

            enabled = plugin_dir.name in self._enabled

            # Plugin class discovery — always loaded, not gated by config.plugins.
            plugin_yaml = plugin_dir / "plugin.yaml"
            plugin_config = {}
            if plugin_yaml.exists():
                plugin_config = yaml.safe_load(plugin_yaml.read_text()) or {}

            plugin_py = plugin_dir / "plugin.py"
            if plugin_py.exists():
                try:
                    mod = importlib.import_module(
                        f"arf.plugins.{plugin_dir.name}.plugin"
                    )
                    for attr in dir(mod):
                        obj = getattr(mod, attr)
                        if (isinstance(obj, type)
                                and hasattr(obj, "name")
                                and hasattr(obj, "hooks")
                                and attr.endswith("Plugin")):
                            cfg = plugin_config.get("config", {})
                            instance = obj(cfg)
                            self._scanned_plugins.append(instance)
                            logger.info("Loaded plugin '%s' from %s",
                                        instance.name, plugin_dir.name)
                            break
                except Exception as e:
                    logger.warning("Failed to load plugin from %s: %s",
                                   plugin_dir.name, e)

            # Tools, skills, hooks — gated by config.plugins.
            if not enabled:
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

            hooks_dir = plugin_dir / "hooks"
            if hooks_dir.exists() and hooks_dir.is_dir():
                for hook_file in sorted(hooks_dir.iterdir()):
                    if not hook_file.suffix == ".py":
                        continue
                    if hook_file.stem.startswith("_"):
                        continue
                    hook_name = f"{plugin_dir.name}__{hook_file.stem}"
                    self._scanned_hooks.append(HookDefinition(
                        name=hook_name,
                        type=hook_file.stem,
                        run=[f"{_sys.executable} {hook_file}"],
                        env={
                            "ARF_PLUGIN_CONFIG": json.dumps(plugin_config),
                        },
                    ))
            if plugin_py.exists():
                try:
                    mod = importlib.import_module(
                        f"arf.plugins.{plugin_dir.name}.plugin"
                    )
                    for attr in dir(mod):
                        obj = getattr(mod, attr)
                        if (isinstance(obj, type)
                                and hasattr(obj, "name")
                                and hasattr(obj, "hooks")
                                and attr.endswith("Plugin")):
                            cfg = plugin_config.get("config", {})
                            instance = obj(cfg)
                            self._scanned_plugins.append(instance)
                            logger.info("Loaded plugin '%s' from %s",
                                        instance.name, plugin_dir.name)
                            break
                except Exception as e:
                    logger.warning("Failed to load plugin from %s: %s",
                                   plugin_dir.name, e)

        # Collect tools from all enabled plugin tool providers
        for tp in self._tool_providers.values():
            self._scanned_tools.extend(tp.list())

        # Collect skills from all enabled plugin skill providers
        for sp in self._skill_providers.values():
            self._scanned_skills.extend(sp.list())

    def list_tools(self) -> list[ToolConfig]:
        if not self._loaded:
            self._load()
        return list(self._scanned_tools)

    def list_skills(self) -> list[SkillConfig]:
        if not self._loaded:
            self._load()
        return list(self._scanned_skills)

    def list_hooks(self) -> list[HookDefinition]:
        """Return HookDefinitions for all scanned plugin hooks."""
        if not self._loaded:
            self._load()
        return list(self._scanned_hooks)

    def list_plugins(self) -> list:
        """Return auto-discovered plugin class instances (in-process hooks)."""
        if not self._loaded:
            self._load()
        return list(self._scanned_plugins)

    async def execute(self, name: str, params: dict) -> ToolResult | None:
        """Try to execute a plugin tool. Returns None if not found."""
        if not self._loaded:
            self._load()
        for tp in self._tool_providers.values():
            cfg = await tp.resolve(name)
            if cfg is not None:
                return await tp.execute(name, params)
        return None
