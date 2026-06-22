"""PluginProvider — loads tools/skills from arf/plugins/{name}/tools/."""
from __future__ import annotations
import importlib.util
import logging
from pathlib import Path
import yaml

from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult
from arf.resources.cache import ResourceCache

logger = logging.getLogger("arf.plugins.discovery")


class PluginProvider:
    """Scans enabled plugin directories for tools and skills."""

    def __init__(self, plugins_dir: str | Path, enabled: list[str] | None = None,
                 plugin_configs: dict | None = None):
        self._root = Path(plugins_dir)
        self._enabled = set(enabled or [])
        self._plugin_configs = plugin_configs or {}
        self._cache = ResourceCache()
        self._functions: dict[str, object] = {}
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        self._cache.invalidate()
        self._functions.clear()
        if not self._root.exists():
            return

        for plugin_name in sorted(self._enabled):
            tools_dir = self._root / plugin_name / "tools"
            if not tools_dir.is_dir():
                continue
            for tool_dir in sorted(tools_dir.iterdir()):
                if not tool_dir.is_dir():
                    continue
                yaml_path = tool_dir / "tool.yaml"
                if not yaml_path.is_file():
                    continue
                try:
                    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                    cfg = ToolConfig(**raw)
                except Exception:
                    logger.warning("Failed to load plugin tool %s/%s", plugin_name, tool_dir.name, exc_info=True)
                    continue

                # Key by bare name for internal lookup
                self._cache.put(cfg.name, cfg)

                # Load function.py if present
                func_path = tool_dir / "function.py"
                if func_path.exists():
                    try:
                        spec = importlib.util.spec_from_file_location(
                            f"arf_plugin_{plugin_name}_{cfg.name}", str(func_path),
                        )
                        if spec and spec.loader:
                            mod = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(mod)
                            if hasattr(mod, "execute"):
                                self._functions[f"{plugin_name}__{cfg.name}"] = mod.execute
                    except Exception:
                        logger.warning("Failed to load function for %s/%s", plugin_name, cfg.name, exc_info=True)

    def list_tools(self) -> list:
        if not self._loaded:
            self._load()
        return self._cache.get_all()

    def list_tools_with_plugin(self) -> list[tuple[str, ToolConfig]]:
        """Return [(plugin_name, ToolConfig), ...] for all loaded plugin tools."""
        if not self._loaded:
            self._load()
        # Group tools by plugin: scan plugin dirs looking for tools
        result: list[tuple[str, ToolConfig]] = []
        for plugin_name in sorted(self._enabled):
            for cfg in self._cache.get_all():
                result.append((plugin_name, cfg))
            # Only add each tool once (break after first plugin match)
            # Actually, tools are keyed by bare name, so we need to map plugin→tools properly
        # Re-implement with proper plugin→tool mapping
        return self._list_with_plugin()

    def _list_with_plugin(self) -> list[tuple[str, ToolConfig]]:
        """Scan plugin dirs and return [(plugin_name, ToolConfig), ...]."""
        if not self._loaded:
            self._load()
        result: list[tuple[str, ToolConfig]] = []
        seen: set[str] = set()
        for plugin_name in sorted(self._enabled):
            tools_dir = self._root / plugin_name / "tools"
            if not tools_dir.is_dir():
                continue
            for tool_dir in sorted(tools_dir.iterdir()):
                if not tool_dir.is_dir():
                    continue
                yaml_path = tool_dir / "tool.yaml"
                if not yaml_path.is_file():
                    continue
                try:
                    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                    cfg = ToolConfig(**raw)
                    key = f"{plugin_name}__{cfg.name}"
                    if key not in seen:
                        seen.add(key)
                        result.append((plugin_name, cfg))
                except Exception:
                    continue
        return result

    def list_plugins(self) -> list:
        return list(self._enabled)

    def list_hooks(self) -> list:
        return []

    def list_skills(self) -> list:
        return []

    async def execute_plugin_tool(self, tool_name: str, params: dict) -> ToolResult | None:
        """Execute a plugin tool by namespaced name (e.g. filesystem__read_text_file).

        Normalizes the return value to ToolResult so callers always see
        .success / .data / .error attributes regardless of what the plugin
        function returns.
        """
        fn = self._functions.get(tool_name)
        if fn is None:
            return None
        try:
            result = await fn(**params)
        except Exception as exc:
            return ToolResult(
                tool_name=tool_name, success=False,
                error=str(exc))
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, dict):
            return ToolResult(
                tool_name=tool_name,
                success=result.get("ok", result.get("success", False)),
                data=result,
                error=result.get("error"),
            )
        return ToolResult(
            tool_name=tool_name, success=True, data={"result": result})
