"""Integration tests using real tool directories and FakeModelAdapter.

These tests verify the full execution pipeline — provider -> resolver -> merge ->
inventory -> system prompt — using real filesystem data, so that bugs like
empty description override or missing plugin tools are caught.
"""
import asyncio
import pytest
from tests.fixtures.fake_model_adapter import FakeModelAdapter, FakeResponse


class TestToolInventoryCompleteness:
    """Verify that the inventory built from real tools has descriptions."""

    def test_all_tools_have_descriptions(self, resolver):
        """Every tool should have a non-empty description."""
        tools = resolver.get_tool_definitions_sync()
        assert len(tools) >= 10, f"Expected at least 10 tools, got {len(tools)}"
        for t in tools:
            assert t.description, f"Tool {t.name} has empty description"

    def test_all_tools_have_parameters(self, resolver):
        """Every tool should have parameters defined."""
        tools = resolver.get_tool_definitions_sync()
        assert len(tools) > 0, "No tools found"
        for t in tools:
            assert t.parameters, f"Tool {t.name} has empty parameters"

    def test_skills_have_descriptions(self, resolver):
        """Skills from filesystem should have non-empty descriptions."""
        skills = resolver.get_skill_definitions_sync()
        assert len(skills) > 0, "No skills found"
        for s in skills:
            assert s.description, f"Skill {s.name} has empty description"

    def test_merge_preserves_filesystem_descriptions(self, resolver):
        """Agent.yaml overrides must not clear descriptions from tool.yaml."""
        tools = resolver.get_tool_definitions_sync()
        writer = next(t for t in tools if t.name == "file_writer")
        assert writer.description, "file_writer should have description from tool.yaml"
        assert "Create" in writer.description or "file" in writer.description.lower()


class TestEngineToolExecution:
    """Verify tools execute without _engine contamination."""

    def test_file_writer_executes_without_engine_error_direct(self, resolver):
        """file_writer should not receive unexpected _engine kwarg."""
        async def run():
            result = await resolver.execute("file_writer", {"path": "test.md", "content": "# test"})
            assert result.success, f"file_writer failed: {result.error}"
            assert "unexpected keyword argument" not in str(result.error or "")
        asyncio.run(run())

    def test_python_exec_executes_without_engine_error(self, resolver, tmp_path):
        """python_exec should accept script path parameter without _engine error."""
        script = tmp_path / "hello.py"
        script.write_text("print('hello')")
        async def run():
            result = await resolver.execute("python_exec", {"script": str(script)})
            error = str(result.error or "")
            assert "unexpected keyword argument" not in error, f"Got error: {error}"
        asyncio.run(run())

    def test_file_writer_executes_without_engine_error(self, resolver, tmp_path):
        """file_writer should execute without _engine contamination."""
        import os
        test_file = tmp_path / "test_write.txt"
        async def run():
            result = await resolver.execute("file_writer", {
                "path": str(test_file), "content": "hello world"
            })
            error = str(result.error or "")
            assert "unexpected keyword argument" not in error, f"Got: {error}"
        asyncio.run(run())


class TestSystemPromptInventory:
    """Verify system prompt assembly via DefaultSystemPromptProvider."""

    _sp = None

    @pytest.fixture(autouse=True)
    def _build_inventory(self, resolver, agent_config):
        """Mirrors BaseAgent init: merge tool/skill defs, then build."""
        merged_specs = resolver.get_tool_definitions_sync()
        if merged_specs:
            from arf.core.config_base import ToolConfig as _ToolConfig
            merged_tools = []
            for td in merged_specs:
                d = td if isinstance(td, dict) else td.model_dump()
                merged_tools.append(_ToolConfig(**d))
            agent_config.tools = merged_tools

        merged_skills = resolver.get_skill_definitions_sync()
        if merged_skills:
            agent_config.skills = merged_skills

        from arf.agent.default_prompt_provider import DefaultSystemPromptProvider
        provider = DefaultSystemPromptProvider(config=agent_config)
        self._sp = provider.build()

    def test_prefix_is_assembled(self):
        """Prefix should be a non-empty string containing role + rules + workspace."""
        assert len(self._sp.prefix) > 0, "Prefix should be non-empty"

    def test_prefix_role_populated(self):
        """Prefix should contain role from agent.yaml."""
        assert "test assistant" in self._sp.prefix, (
            "Prefix should contain role text"
        )

    def test_prefix_critical_rules_populated(self):
        """Prefix should contain critical rules."""
        assert "R1" in self._sp.prefix or "R2" in self._sp.prefix, (
            "Prefix should contain critical rules"
        )

    def test_prefix_includes_workspace_dir(self):
        """Prefix should include workspace directory."""
        assert "Workspace" in self._sp.prefix or "workspace" in self._sp.prefix.lower(), (
            "Prefix should contain workspace directory"
        )

    def test_sp_is_dataclass_with_prefix_attr(self):
        """SystemPrompt is a dataclass with only a prefix field."""
        assert hasattr(self._sp, "prefix"), "SystemPrompt must have prefix attr"
