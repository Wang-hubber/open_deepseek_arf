"""Tests for PluginProvider plugin resource scanner."""
from pathlib import Path
from unittest.mock import patch, MagicMock
from types import ModuleType
import yaml

import pytest

from arf.resources.providers.plugin_provider import PluginProvider


def _make_mock_plugin_module(counter: list[int], name="test_plugin",
                             hooks=None, mod_name="testplugin"):
    """Create a mock module with a Plugin class that tracks instantiation count."""
    mod = ModuleType(f"arf.plugins.{mod_name}")
    mod.__package__ = f"arf.plugins.{mod_name}"

    class TrackedPlugin:
        def __init__(self, config=None):
            counter.append(1)
            self._config = config
            self._name = name
            self._hooks = hooks or {"round_end": "side"}

        @property
        def name(self) -> str:
            return self._name

        @property
        def hooks(self) -> dict[str, str]:
            return self._hooks

        def on_hook(self, hook_name, context):
            pass

    # The discovery code looks for classes ending in "Plugin"
    mod.TrackedPlugin = TrackedPlugin
    return mod


class TestPluginProvider:
    @pytest.fixture
    def plugins_root(self, tmp_path):
        """Create a temp plugins dir with planner + todo plugins."""
        root = tmp_path / "plugins"
        root.mkdir()

        # planner plugin
        planner = root / "planner" / "tools" / "planner"
        planner.mkdir(parents=True)
        (planner / "tool.yaml").write_text(yaml.dump({
            "name": "planner",
            "description": "Decompose a task into actionable steps",
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"],
            },
        }), encoding="utf-8")
        (planner / "function.py").write_text(
            "async def execute(task: str = '', _engine=None) -> dict:\n"
            "    return {'steps': [task]}\n",
            encoding="utf-8",
        )

        # planner skill
        skills_d = root / "planner" / "skills"
        skills_d.mkdir(parents=True)
        (skills_d / "plan_execute.yaml").write_text(yaml.dump({
            "name": "plan_execute",
            "description": "Task planning skill",
            "prompt": "Use planner to decompose tasks.",
            "activation": "kernel",
        }), encoding="utf-8")

        # todo plugin (no skills)
        todo = root / "todo" / "tools" / "todo"
        todo.mkdir(parents=True)
        (todo / "tool.yaml").write_text(yaml.dump({
            "name": "todo",
            "description": "Manage task list",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "check", "clear"]},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["action"],
            },
        }), encoding="utf-8")
        (todo / "function.py").write_text(
            "async def execute(action: str, items: list[str] = None) -> dict:\n"
            "    return {'action': action, 'items': items or []}\n",
            encoding="utf-8",
        )

        return root

    def test_scans_enabled_plugin_tools(self, plugins_root):
        provider = PluginProvider(plugins_root, ["planner", "todo"])
        tools = provider.list_tools()
        names = {t.name for t in tools}
        assert "planner" in names
        assert "todo" in names

    def test_ignores_disabled_plugins(self, plugins_root):
        provider = PluginProvider(plugins_root, ["planner"])
        tools = provider.list_tools()
        names = {t.name for t in tools}
        assert "planner" in names
        assert "todo" not in names

    def test_scans_plugin_skills(self, plugins_root):
        provider = PluginProvider(plugins_root, ["planner"])
        skills = provider.list_skills()
        names = {s.name for s in skills}
        assert "plan_execute" in names

    def test_empty_plugins_list_returns_empty(self, plugins_root):
        provider = PluginProvider(plugins_root, [])
        assert provider.list_tools() == []
        assert provider.list_skills() == []

    def test_nonexistent_plugin_skipped(self, plugins_root):
        provider = PluginProvider(plugins_root, ["nonexistent", "planner"])
        tools = provider.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "planner"


class TestPluginClassDoubleInstantiation:
    """P1: PluginProvider._load() instantiates the same plugin class twice
    when the plugin is enabled — once in the always-loaded pass (lines 73-96)
    and again in the enabled-gated pass (lines 130-152).

    After the fix, list_plugins() returns exactly one instance per plugin.
    """

    @pytest.fixture
    def plugins_root(self, tmp_path):
        root = tmp_path / "plugins"
        root.mkdir()
        return root

    def _create_plugin_dir(self, root: Path, name: str, with_yaml: bool = True):
        """Create a minimal plugin directory with plugin.py marker file."""
        pdir = root / name
        pdir.mkdir(parents=True)
        (pdir / "plugin.py").touch()
        if with_yaml:
            (pdir / "plugin.yaml").write_text(
                yaml.dump({"name": name, "version": "1.0"}), encoding="utf-8")
        return pdir

    def test_enabled_plugin_instantiated_once(self, plugins_root):
        """An enabled plugin with plugin.py must produce exactly 1 instance."""
        self._create_plugin_dir(plugins_root, "myplugin")

        counter: list[int] = []
        mock_mod = _make_mock_plugin_module(counter, name="myplugin",
                                            mod_name="myplugin")

        with patch(
            "arf.resources.providers.plugin_provider.importlib.import_module",
            return_value=mock_mod,
        ):
            provider = PluginProvider(str(plugins_root), ["myplugin"])
            plugins = provider.list_plugins()

        assert len(plugins) == 1, (
            f"DOUBLE INSTANTIATION: expected 1 plugin instance, "
            f"got {len(plugins)}. The class was instantiated {sum(counter)} time(s)."
        )
        assert sum(counter) == 1, (
            f"Plugin class __init__ called {sum(counter)} times, expected 1"
        )

    def test_disabled_plugin_still_discovered(self, plugins_root):
        """Disabled plugins still have class loaded (always-loaded pass),
        but tools/skills are skipped."""
        self._create_plugin_dir(plugins_root, "disabled_plugin")

        counter: list[int] = []
        mock_mod = _make_mock_plugin_module(counter, name="disabled_plugin",
                                            mod_name="disabled_plugin")

        with patch(
            "arf.resources.providers.plugin_provider.importlib.import_module",
            return_value=mock_mod,
        ):
            provider = PluginProvider(str(plugins_root), [])
            plugins = provider.list_plugins()

        assert len(plugins) == 1, (
            f"Disabled plugin should still be discovered once, "
            f"got {len(plugins)}"
        )
        assert sum(counter) == 1, (
            f"Disabled plugin instantiated {sum(counter)} times, expected 1"
        )

    def test_two_enabled_plugins_each_instantiated_once(self, plugins_root):
        """Two enabled plugins: 2 instances total, not 4."""
        for name in ("plug_a", "plug_b"):
            self._create_plugin_dir(plugins_root, name)

        counter: list[int] = []

        def _make_mod(mod_name):
            return _make_mock_plugin_module(counter, name=mod_name,
                                            mod_name=mod_name)

        mods = {"plug_a": _make_mod("plug_a"), "plug_b": _make_mod("plug_b")}

        def _import_side_effect(module_name):
            for key, mod in mods.items():
                if key in module_name:
                    return mod
            raise ModuleNotFoundError(f"No module named '{module_name}'")

        with patch(
            "arf.resources.providers.plugin_provider.importlib.import_module",
            side_effect=_import_side_effect,
        ):
            provider = PluginProvider(
                str(plugins_root), ["plug_a", "plug_b"])
            plugins = provider.list_plugins()

        assert len(plugins) == 2, (
            f"Expected 2 plugin instances (1 each), got {len(plugins)}"
        )
        assert sum(counter) == 2, (
            f"Expected 2 instantiations total, got {sum(counter)}"
        )
