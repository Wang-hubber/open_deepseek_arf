"""Tests that ResourceResolver._merge_configs correctly handles empty overrides.

Previously, agent.yaml overrides with empty strings/dicts (from simplified
tool declarations) would overwrite filesystem descriptions. These tests
verify the fix.
"""
import pytest
from arf.resources.resolver import ResourceResolver
from arf.core.config_base import ToolConfig, SkillConfig


class TestMergeEmptyOverrides:
    """Verify _merge_configs preserves filesystem values when overrides are empty."""

    def test_empty_description_does_not_override_filesystem(self):
        """Empty string description in override should NOT replace filesystem desc."""
        fs_tools = [ToolConfig(name="my_tool", description="Read files", parameters={"type": "object"})]
        overrides = [{"name": "my_tool", "activation": "kernel", "description": ""}]

        resolver = ResourceResolver.__new__(ResourceResolver)
        merged = resolver._merge_configs(fs_tools, overrides, ToolConfig)

        assert len(merged) == 1
        assert merged[0].description == "Read files"  # filesystem value preserved
        assert merged[0].activation == "kernel"        # override applied

    def test_empty_parameters_does_not_override_filesystem(self):
        """Empty dict parameters in override should NOT replace filesystem params."""
        fs_tools = [ToolConfig(name="my_tool", description="Run code", parameters={"properties": {"code": {"type": "string"}}})]
        overrides = [{"name": "my_tool", "activation": "discoverable", "parameters": {}}]

        resolver = ResourceResolver.__new__(ResourceResolver)
        merged = resolver._merge_configs(fs_tools, overrides, ToolConfig)

        assert len(merged) == 1
        assert merged[0].parameters == {"properties": {"code": {"type": "string"}}}

    def test_only_activation_override_preserves_all_filesystem_fields(self):
        """Simplified override (name + activation only) preserves description and params."""
        fs_tools = [
            ToolConfig(name="file_reader", description="Read file contents", parameters={"properties": {"path": {"type": "string"}}}),
            ToolConfig(name="web_search", description="Search internet", parameters={"properties": {"query": {"type": "string"}}}),
        ]
        overrides = [
            {"name": "file_reader", "activation": "kernel"},
            {"name": "web_search", "activation": "discoverable"},
        ]

        resolver = ResourceResolver.__new__(ResourceResolver)
        merged = resolver._merge_configs(fs_tools, overrides, ToolConfig)

        assert len(merged) == 2
        assert merged[0].name == "file_reader"
        assert merged[0].description == "Read file contents"
        assert merged[0].activation == "kernel"
        assert merged[1].name == "web_search"
        assert merged[1].description == "Search internet"
        assert merged[1].activation == "discoverable"

    def test_skills_merge_handles_empty_overrides(self):
        """Skills merge should also filter empty overrides."""
        fs_skills = [SkillConfig(name="code_review", description="Review code", tools=["file_reader"])]
        overrides = [{"name": "code_review", "activation": "discoverable"}]

        resolver = ResourceResolver.__new__(ResourceResolver)
        merged = resolver._merge_configs(fs_skills, overrides, SkillConfig)

        assert len(merged) == 1
        assert merged[0].description == "Review code"
        assert merged[0].activation == "discoverable"
        assert merged[0].tools == ["file_reader"]

    def test_override_only_entries_without_filesystem_base(self):
        """Overrides without matching filesystem items are appended."""
        fs_tools = [ToolConfig(name="existing", description="Exists")]
        overrides = [{"name": "new_tool", "activation": "kernel", "description": "New tool"}]

        resolver = ResourceResolver.__new__(ResourceResolver)
        merged = resolver._merge_configs(fs_tools, overrides, ToolConfig)

        assert len(merged) == 2
        assert merged[1].name == "new_tool"
        assert merged[1].description == "New tool"
