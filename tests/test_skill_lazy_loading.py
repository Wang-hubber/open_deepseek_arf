"""Tests for Skill lazy loading system."""
import tempfile
from pathlib import Path
import pytest
from arf.skills.skill_index import SkillIndex, SkillEntry


class TestSkillIndex:
    def test_scan_discovers_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "react-component"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.yaml").write_text(
                "name: react-component\n"
                "description: 创建 React 组件\n"
                "tools_sequence:\n"
                "  - plan_create\n"
                "  - plan_dispatch\n"
            )
            (skill_dir / "skill.md").write_text(
                "# React Component Guidelines\n\nUse Zustand for state."
            )

            index = SkillIndex(project_root=tmp)
            index.scan()

            entry = index.resolve("react-component")
            assert entry is not None
            assert entry.name == "react-component"
            assert entry.description == "创建 React 组件"
            assert entry.tools_sequence == ["plan_create", "plan_dispatch"]

            body = index.load_body("react-component")
            assert body == "# React Component Guidelines\n\nUse Zustand for state."

    def test_resolve_unknown_skill_returns_none(self):
        index = SkillIndex(project_root="/nonexistent")
        index.scan()
        assert index.resolve("unknown") is None

    def test_load_body_nonexistent_skill_returns_none(self):
        index = SkillIndex(project_root="/nonexistent")
        index.scan()
        assert index.load_body("unknown") is None

    def test_format_index_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "api-endpoint"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.yaml").write_text(
                "name: api-endpoint\n"
                "description: 创建 REST API 端点\n"
            )

            index = SkillIndex(project_root=tmp)
            index.scan()

            md = index.format_index_markdown()
            assert "## Available Skills" in md
            assert "api-endpoint" in md
            assert "创建 REST API 端点" in md

    def test_plugin_skills_override_project_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj_skill = Path(tmp) / "skills" / "my-skill"
            proj_skill.mkdir(parents=True)
            (proj_skill / "skill.yaml").write_text(
                "name: my-skill\ndescription: project version\n"
            )

            plugin_skill = Path(tmp) / "arf" / "plugins" / "test-plugin" / "skills" / "my-skill"
            plugin_skill.mkdir(parents=True)
            (plugin_skill / "skill.yaml").write_text(
                "name: my-skill\ndescription: plugin version\n"
            )

            index = SkillIndex(project_root=tmp)
            index.scan()

            entry = index.resolve("my-skill")
            assert entry.description == "plugin version"
