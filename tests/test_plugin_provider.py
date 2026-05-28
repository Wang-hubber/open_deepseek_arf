"""Tests for PluginProvider plugin resource scanner."""
from pathlib import Path
import yaml

import pytest

from arf.resources.providers.plugin_provider import PluginProvider


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
