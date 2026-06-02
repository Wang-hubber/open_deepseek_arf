"""PluginLoader — discover and instantiate plugins from the plugins/ directory."""
import importlib.util
import logging
from pathlib import Path
from typing import Any
import yaml

from arf.core.protocols.plugin import PluginProtocol

logger = logging.getLogger("arf.plugins.loader")

PLUGINS_DIR = Path(__file__).parent


def discover_manifests() -> list[dict[str, Any]]:
    """Scan plugins/ directory for plugin.yaml files. Return manifest list."""
    manifests: list[dict[str, Any]] = []
    for item in sorted(PLUGINS_DIR.iterdir()):
        if not item.is_dir():
            continue
        if item.name.startswith("_") or item.name.startswith("."):
            continue
        manifest_path = item / "plugin.yaml"
        if not manifest_path.exists():
            manifest_path = item / "config.yaml"  # legacy fallback
        if not manifest_path.exists():
            continue
        try:
            with open(manifest_path) as f:
                manifest = yaml.safe_load(f)
            if manifest:
                manifest["_dir"] = str(item)
                manifests.append(manifest)
        except Exception:
            logger.exception("Failed to load plugin manifest: %s", manifest_path)
    return manifests


def load_plugin(manifest: dict[str, Any]) -> PluginProtocol | None:
    """Instantiate a plugin from its manifest.

    Looks for plugin.py in the plugin directory. If found, imports it
    and returns the first PluginProtocol implementation found.

    Falls back to None if no in-process handler exists (the plugin
    may use subprocess-based hooks via SubprocessHookRunner instead).
    """
    name = manifest.get("name", "")
    plugin_dir = Path(manifest["_dir"])
    plugin_module_path = plugin_dir / "plugin.py"

    if not plugin_module_path.exists():
        return None

    try:
        spec = importlib.util.spec_from_file_location(
            f"arf_plugin_{name}", str(plugin_module_path)
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type)
                    and attr.__name__.endswith("Plugin")
                    and hasattr(attr, "on_hook")
                    and attr is not PluginProtocol):
                return attr(manifest.get("config", {}))
    except Exception:
        logger.exception("Failed to load plugin '%s'", name)

    return None


def load_all_plugins() -> list[PluginProtocol]:
    """Discover and load all enabled in-process plugins."""
    manifests = discover_manifests()
    plugins: list[PluginProtocol] = []
    for m in manifests:
        if not m.get("enabled", True):
            logger.info("Plugin '%s' is disabled, skipping", m.get("name"))
            continue
        plugin = load_plugin(m)
        if plugin:
            plugins.append(plugin)
            logger.info("Loaded plugin: %s on hooks %s", plugin.name, plugin.hooks)
    return plugins
