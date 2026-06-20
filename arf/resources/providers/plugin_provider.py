"""PluginProvider — thin stub. Use arf.harness.loader for new development."""
from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger("arf.plugins.discovery")


class PluginProvider:
    """Legacy plugin provider stub. Use arf.harness.loader instead."""

    def __init__(self, plugins_dir: str | Path, enabled: list[str] | None = None,
                 plugin_configs: dict | None = None):
        self._root = Path(plugins_dir)
        self._enabled = set(enabled or [])
        self._plugin_configs = plugin_configs or {}
        self._loaded = False

    def list_plugins(self) -> list:
        return []

    def list_hooks(self) -> list:
        return []

    def list_tools(self) -> list:
        return []

    def list_skills(self) -> list:
        return []

    def list_tools_with_plugin(self) -> list:
        return []
