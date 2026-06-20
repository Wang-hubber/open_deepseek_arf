"""Tests for HarnessConfig and PluginLoader."""
import tempfile
import os
import pytest
from arf.harness.config import HarnessConfig, ToolSource
from arf.harness.loader import load_plugin_yaml, discover_plugins, instantiate_plugins
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext


class TestHarnessConfig:
    def test_load_minimal_config(self):
        yaml_content = """
plugins:
  - trace
max_turns: 20
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        try:
            cfg = HarnessConfig.from_yaml(path)
            assert cfg.plugins == ["trace"]
            assert cfg.max_turns == 20
        finally:
            os.unlink(path)

    def test_load_with_tool_sources(self):
        yaml_content = """
plugins:
  - compaction
  - trace
tools:
  - type: "directory"
    path: "./tools"
  - type: "kernel"
    names: ["use_skill", "ask_user"]
max_turns: 30
tool_timeout: 120.0
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        try:
            cfg = HarnessConfig.from_yaml(path)
            assert cfg.plugins == ["compaction", "trace"]
            assert len(cfg.tools) == 2
            assert cfg.tools[0].type == "directory"
            assert cfg.tools[1].names == ["use_skill", "ask_user"]
            assert cfg.max_turns == 30
            assert cfg.tool_timeout == 120.0
        finally:
            os.unlink(path)

    def test_default_values(self):
        cfg = HarnessConfig()
        assert cfg.plugins == []
        assert cfg.max_turns == 50
        assert cfg.tool_timeout == 60.0

    def test_tool_source_defaults(self):
        ts = ToolSource(type="directory")
        assert ts.type == "directory"
        assert ts.path == ""
        assert ts.url == ""
        assert ts.names == []


class TestPluginLoader:
    def test_discover_plugins(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        trace_dir = plugin_dir / "trace"
        trace_dir.mkdir(parents=True)
        (trace_dir / "plugin.yaml").write_text("""
name: trace
events:
  - {hook_name: "after_model", event_name: "trace_model", mode: "side"}
config:
  output: jsonl
""")

        configs = discover_plugins(str(plugin_dir), ["trace"])
        assert len(configs) == 1
        assert configs[0]["name"] == "trace"
        assert configs[0]["events"][0]["hook_name"] == "after_model"

    def test_discover_plugins_missing(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        configs = discover_plugins(str(plugin_dir), ["nonexistent"])
        assert configs == []

    def test_discover_plugins_partial(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        trace_dir = plugin_dir / "trace"
        trace_dir.mkdir(parents=True)
        (trace_dir / "plugin.yaml").write_text("""
name: trace
events: []
""")

        configs = discover_plugins(str(plugin_dir), ["trace", "missing"])
        assert len(configs) == 1
        assert configs[0]["name"] == "trace"

    def test_instantiate_plugins(self):
        class TestPlugin(Plugin):
            def __init__(self, name="test", events=None, config=None):
                super().__init__(name=name, events=events or [], config=config or {})
                self.initialized = True

            async def handle(self, event_name: str, ctx: PluginContext) -> None:
                pass

        configs = [
            {"name": "test", "events": [{"hook_name": "after_model", "event_name": "log", "mode": "side"}], "config": {"x": 1}},
        ]
        plugins = instantiate_plugins(configs, plugin_classes={"test": TestPlugin})
        assert len(plugins) == 1
        assert plugins[0].name == "test"
        assert plugins[0].config == {"x": 1}
        assert plugins[0].initialized is True

    def test_instantiate_plugins_unknown_name(self):
        configs = [{"name": "missing", "events": [], "config": {}}]
        plugins = instantiate_plugins(configs)
        assert plugins == []  # Silently skipped
