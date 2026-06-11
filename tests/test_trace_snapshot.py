"""Tests for EnvSnapshotBuilder."""
import tempfile
from pathlib import Path

import pytest

from arf.plugins.trace.snapshot import EnvSnapshotBuilder


class TestEnvSnapshotBuilder:
    @pytest.fixture
    def plugins_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # Create a plugin with tool
            p = root / "file_tools" / "tools" / "read"
            p.mkdir(parents=True)
            (root / "file_tools" / "plugin.yaml").write_text(
                "name: file_tools\nenabled: true\n", encoding="utf-8"
            )
            (p / "tool.yaml").write_text(
                "name: read\ndescription: read file\n", encoding="utf-8"
            )
            (p / "function.py").write_text(
                "async def execute(path: str) -> dict:\n    return {}\n",
                encoding="utf-8",
            )

            # Create a plugin with skill
            s = root / "planner" / "skills"
            s.mkdir(parents=True)
            (root / "planner" / "plugin.yaml").write_text(
                "name: planner\nenabled: true\n", encoding="utf-8"
            )
            (s / "plan_execute.yaml").write_text(
                "name: plan_execute\ndescription: Plan tasks\n",
                encoding="utf-8",
            )

            yield root

    @pytest.fixture
    def extra_file(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "agent.yaml"
            f.write_text("system_prompt: You are helpful\n", encoding="utf-8")
            yield f

    def test_build_returns_xml_and_hash(self, plugins_root):
        builder = EnvSnapshotBuilder(str(plugins_root))
        xml_str, hash_val = builder.build()
        assert isinstance(xml_str, str)
        assert isinstance(hash_val, str)
        assert len(hash_val) == 12
        assert "<snapshot" in xml_str
        assert f'hash="{hash_val}"' in xml_str

    def test_plugins_included(self, plugins_root):
        builder = EnvSnapshotBuilder(str(plugins_root))
        xml_str, _ = builder.build()
        assert 'name="file_tools"' in xml_str
        assert 'name="planner"' in xml_str

    def test_tool_definition_included(self, plugins_root):
        builder = EnvSnapshotBuilder(str(plugins_root))
        xml_str, _ = builder.build()
        assert 'name="read"' in xml_str
        assert "read file" in xml_str

    def test_tool_implementation_included(self, plugins_root):
        builder = EnvSnapshotBuilder(str(plugins_root))
        xml_str, _ = builder.build()
        assert "async def execute" in xml_str

    def test_skill_included(self, plugins_root):
        builder = EnvSnapshotBuilder(str(plugins_root))
        xml_str, _ = builder.build()
        assert 'name="plan_execute"' in xml_str

    def test_extra_files_included(self, plugins_root, extra_file):
        builder = EnvSnapshotBuilder(str(plugins_root), [str(extra_file)])
        xml_str, _ = builder.build()
        assert "agent.yaml" in xml_str
        assert "You are helpful" in xml_str

    def test_same_config_yields_same_hash(self, plugins_root):
        builder1 = EnvSnapshotBuilder(str(plugins_root))
        builder2 = EnvSnapshotBuilder(str(plugins_root))
        _, h1 = builder1.build()
        _, h2 = builder2.build()
        assert h1 == h2

    def test_different_config_yields_different_hash(self, plugins_root):
        builder1 = EnvSnapshotBuilder(str(plugins_root))
        _, h1 = builder1.build()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("model: gpt4\n")
        try:
            builder2 = EnvSnapshotBuilder(str(plugins_root), [f.name])
            _, h2 = builder2.build()
            assert h1 != h2
        finally:
            Path(f.name).unlink()

    def test_missing_extra_file_skipped(self, plugins_root):
        builder = EnvSnapshotBuilder(str(plugins_root), ["/nonexistent/file.yaml"])
        xml_str, _ = builder.build()
        # should not crash; snapshot should still be valid

    def test_empty_plugins_root(self, plugins_root):
        with tempfile.TemporaryDirectory() as td:
            builder = EnvSnapshotBuilder(td)
            xml_str, _ = builder.build()
            assert "<plugins" in xml_str
