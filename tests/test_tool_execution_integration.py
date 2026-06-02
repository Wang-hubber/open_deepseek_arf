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
    """Verify system prompt inventory includes all expected sections
    via DefaultSystemPromptProvider."""

    _suffix = None
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
        self._suffix = self._sp.suffix

    def test_inventory_placeholder_present(self):
        """Suffix should contain $INVENTORY placeholder for MCP fill."""
        assert "$INVENTORY" in self._suffix, (
            "Missing $INVENTORY placeholder in suffix"
        )

    def test_suffix_is_template_not_rendered(self):
        """Suffix template should NOT have been rendered with tool descriptions.
        Inventory filling is deferred to MCP at startup and cached thereafter."""
        assert "Available Tools" not in self._suffix, (
            "Invetory should NOT be pre-rendered — it's filled by MCP"
        )

    def test_prefix_role_populated(self):
        """Prefix should contain role from agent.yaml."""
        sp = self._sp
        assert len(sp.prefix) > 0, "Prefix should be populated"

    def test_prefix_critical_rules_populated(self):
        """Prefix should contain critical rules."""
        sp = self._sp
        assert "R1" in sp.prefix or "R2" in sp.prefix, (
            "Prefix should contain critical rules"
        )

    def test_full_text_combines_prefix_and_suffix(self):
        """full_text should be prefix + suffix concatenated."""
        sp = self._sp
        assert sp.full_text == sp.prefix + sp.suffix
