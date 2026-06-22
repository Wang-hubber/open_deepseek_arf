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

    def test_plugin_skills_are_namespaced(self):
        """Plugin skills get {plugin}__ prefix — same MCP convention as tools."""
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

            # Both coexist — project with bare name, plugin with namespace prefix
            proj_entry = index.resolve("my-skill")
            assert proj_entry is not None
            assert proj_entry.description == "project version"

            plugin_entry = index.resolve("test-plugin__my-skill")
            assert plugin_entry is not None
            assert plugin_entry.description == "plugin version"

            assert len(index.list_index()) == 2


class TestUseSkillTool:
    @pytest.mark.anyio
    async def test_use_skill_returns_body(self, tmp_path):
        from arf.skills.skill_index import SkillIndex
        import arf.skills.use_skill_tool as use_skill_mod

        # Set up a skill
        skill_dir = tmp_path / "skills" / "react-component"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.yaml").write_text(
            "name: react-component\ndescription: 创建 React 组件\n"
        )
        body = "# React Guidelines\nUse Zustand."
        (skill_dir / "skill.md").write_text(body)

        index = SkillIndex(project_root=str(tmp_path))
        index.scan()
        use_skill_mod._index = index

        result = await use_skill_mod.execute(name="react-component")
        assert isinstance(result, str)
        assert "React Guidelines" in result
        assert "Use Zustand" in result

    @pytest.mark.anyio
    async def test_use_skill_unknown_returns_error(self):
        import arf.skills.use_skill_tool as use_skill_mod
        from arf.skills.skill_index import SkillIndex

        index = SkillIndex(project_root="/nonexistent")
        index.scan()
        use_skill_mod._index = index

        result = await use_skill_mod.execute(name="nonexistent")
        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.anyio
    async def test_use_skill_no_index_returns_error(self):
        import arf.skills.use_skill_tool as use_skill_mod
        use_skill_mod._index = None

        result = await use_skill_mod.execute(name="anything")
        assert result["ok"] is False
        assert "not initialized" in result["error"]

    @pytest.mark.anyio
    async def test_use_skill_missing_body_returns_error(self, tmp_path):
        from arf.skills.skill_index import SkillIndex
        import arf.skills.use_skill_tool as use_skill_mod

        skill_dir = tmp_path / "skills" / "no-body"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.yaml").write_text(
            "name: no-body\ndescription: Has no markdown\n"
        )
        # No skill.md!

        index = SkillIndex(project_root=str(tmp_path))
        index.scan()
        use_skill_mod._index = index

        result = await use_skill_mod.execute(name="no-body")
        assert result["ok"] is False
        assert "skill.md missing" in result["error"]


class TestMessageBuilder:
    def test_build_initial_messages_with_reminder(self):
        from arf.core.message_builder import MessageBuilder

        msgs = MessageBuilder.build_initial_messages(
            system_prompt="You are TestAgent.",
            system_reminder_parts=["## Skills\n- **a**: desc", "## Tools\n- tool1"],
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are TestAgent."
        assert msgs[1]["role"] == "system"
        assert "## Skills" in msgs[1]["content"]
        assert "## Tools" in msgs[1]["content"]

    def test_build_initial_messages_empty_reminder(self):
        from arf.core.message_builder import MessageBuilder

        msgs = MessageBuilder.build_initial_messages(
            system_prompt="You are TestAgent.",
            system_reminder_parts=[],
        )
        assert len(msgs) == 1  # no reminder message if empty

    def test_update_reminder(self):
        from arf.core.message_builder import MessageBuilder

        msgs = [
            {"role": "system", "content": "You are Agent."},
            {"role": "system", "content": "## Old Skills\n- old"},
            {"role": "user", "content": "hi"},
        ]
        MessageBuilder.update_reminder(msgs, "## New Skills\n- new")
        assert msgs[1]["content"] == "## New Skills\n- new"

    def test_update_reminder_removes_when_empty(self):
        from arf.core.message_builder import MessageBuilder

        msgs = [
            {"role": "system", "content": "You are Agent."},
            {"role": "system", "content": "## Skills\n- skill1"},
        ]
        MessageBuilder.update_reminder(msgs, "")
        assert len(msgs) == 1  # reminder removed
        assert msgs[0]["content"] == "You are Agent."

    def test_update_reminder_inserts_when_missing(self):
        from arf.core.message_builder import MessageBuilder

        msgs = [
            {"role": "system", "content": "You are Agent."},
            {"role": "user", "content": "hi"},
        ]
        MessageBuilder.update_reminder(msgs, "## Skills\n- new")
        assert len(msgs) == 3
        assert msgs[1]["content"] == "## Skills\n- new"
