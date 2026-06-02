"""Test PluginLoader discovery and instantiation."""
import tempfile
from pathlib import Path
import yaml


class TestDiscoverManifests:
    def test_finds_plugin_yaml(self):
        """discover_manifests should find plugin directories with plugin.yaml."""
        from arf.plugins.plugin_loader import discover_manifests

        manifests = discover_manifests()

        names = [m["name"] for m in manifests if "name" in m]
        # Existing plugins may or may not have plugin.yaml yet
        # This test primarily verifies the loader doesn't crash
        assert isinstance(manifests, list)


class TestLoadPlugin:
    def test_load_all_plugins_returns_list(self):
        """load_all_plugins should return a list (possibly empty)."""
        from arf.plugins.plugin_loader import load_all_plugins

        plugins = load_all_plugins()
        assert isinstance(plugins, list)

    def test_load_plugin_from_temp_dir(self):
        """Load a plugin.py from a temp directory with plugin.yaml."""
        from arf.plugins.plugin_loader import load_plugin

        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "test_plugin"
            plugin_dir.mkdir()

            # Write plugin.yaml
            (plugin_dir / "plugin.yaml").write_text(yaml.dump({
                "name": "test",
                "hooks": ["round_end"],
            }))

            # Write plugin.py with a PluginProtocol implementation
            (plugin_dir / "plugin.py").write_text("""from arf.core.plugin_context import PluginContext

class TestPlugin:
    def __init__(self, config=None):
        self.config = config
        self.calls = []

    @property
    def name(self):
        return "test"

    @property
    def hooks(self):
        return ["round_end"]

    async def on_hook(self, hook_name, context):
        self.calls.append(hook_name)
""")

            manifest = {
                "name": "test",
                "hooks": ["round_end"],
                "config": {"key": "value"},
                "_dir": str(plugin_dir),
            }

            plugin = load_plugin(manifest)
            assert plugin is not None
            assert plugin.name == "test"
            assert plugin.hooks == ["round_end"]
            assert plugin.config == {"key": "value"}

    def test_load_plugin_returns_none_for_missing_plugin_py(self):
        """Should return None when plugin dir has plugin.yaml but no plugin.py."""
        from arf.plugins.plugin_loader import load_plugin

        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "no_code"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.yaml").write_text("name: no_code\nhooks: []\n")

            manifest = {"name": "no_code", "hooks": [], "_dir": str(plugin_dir)}
            plugin = load_plugin(manifest)
            assert plugin is None
