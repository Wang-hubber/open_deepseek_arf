"""Tests for ResourceResolver snapshot methods — list_tools() and list_skills()."""
import pytest
from arf.resources.resolver import ResourceResolver
from arf.core.config_base import ToolConfig, SkillConfig


class FakeToolProvider:
    def list(self) -> list[ToolConfig]:
        return [
            ToolConfig(name="read_file", description="Read a file",
                       parameters={"path": {"type": "string"}}),
            ToolConfig(name="write_file", description="Write a file",
                       parameters={"path": {"type": "string"}, "content": {"type": "string"}}),
        ]


class FakeSkillProvider:
    def list(self) -> list[SkillConfig]:
        return [
            SkillConfig(name="code_review", description="Review code for bugs"),
        ]


class TestResourceRegistrySnapshot:
    def test_list_tools_returns_name_keyed_dict(self):
        resolver = ResourceResolver(tool_provider=FakeToolProvider())
        tools = resolver.list_tools()
        assert isinstance(tools, dict)
        assert set(tools.keys()) == {"read_file", "write_file"}
        assert tools["read_file"]["description"] == "Read a file"
        assert tools["read_file"]["parameters"] == {"path": {"type": "string"}}
        assert "name" not in tools["read_file"]

    def test_list_tools_returns_empty_dict_for_none_provider(self):
        resolver = ResourceResolver(tool_provider=None)
        assert resolver.list_tools() == {}

    def test_list_skills_returns_name_keyed_dict(self):
        resolver = ResourceResolver(tool_provider=None, skill_provider=FakeSkillProvider())
        skills = resolver.list_skills()
        assert isinstance(skills, dict)
        assert set(skills.keys()) == {"code_review"}
        assert skills["code_review"]["description"] == "Review code for bugs"

    def test_list_skills_returns_empty_dict_for_none_provider(self):
        resolver = ResourceResolver(tool_provider=None, skill_provider=None)
        assert resolver.list_skills() == {}
