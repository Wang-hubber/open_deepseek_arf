"""PluginLoader — find and parse plugin.yaml files."""
from __future__ import annotations
from pathlib import Path
from typing import Any


def load_plugin_yaml(plugin_dir: str, name: str) -> dict | None:
    """Load a single plugin's plugin.yaml by name. Returns config dict or None."""
    path = Path(plugin_dir) / name / "plugin.yaml"
    if not path.exists():
        return None
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def discover_plugins(plugin_dir: str, enabled: list[str]) -> list[dict]:
    """Discover enabled plugins from plugin_dir. Returns list of plugin configs."""
    configs = []
    for name in enabled:
        cfg = load_plugin_yaml(plugin_dir, name)
        if cfg:
            cfg.setdefault("name", name)
            configs.append(cfg)
    return configs


def instantiate_plugins(configs: list[dict], plugin_classes: dict[str, type] | None = None) -> list[Any]:
    """Instantiate plugins from configs.

    Looks up plugin_classes by name. Falls back to importing from
    arf.plugins.<name> if not found in plugin_classes.
    """
    plugins = []
    for cfg in configs:
        name = cfg["name"]
        events = cfg.get("events", [])
        config = cfg.get("config", {})

        cls = None
        if plugin_classes and name in plugin_classes:
            cls = plugin_classes[name]

        if cls is not None:
            plugins.append(cls(name=name, events=events, config=config))
        else:
            # Try dynamic import
            try:
                mod = __import__(f"arf.plugins.{name}", fromlist=["Plugin"])
                plugin_cls = getattr(mod, "Plugin", None)
                if plugin_cls:
                    plugins.append(plugin_cls(name=name, events=events, config=config))
            except ImportError:
                pass

    return plugins
