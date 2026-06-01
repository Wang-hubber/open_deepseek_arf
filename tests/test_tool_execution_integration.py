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

    def test_all_kernel_tools_have_descriptions(self, resolver):
        """Every kernel tool should have a non-empty description."""
        tools = resolver.get_tool_definitions_sync()
        kernel = [t for t in tools if t.activation == "kernel"]
        assert len(kernel) > 0, "No kernel tools found"
        for t in kernel:
            assert t.description, f"Tool {t.name} has empty description"
            assert t.parameters, f"Tool {t.name} has empty parameters"

    def test_all_tools_have_descriptions(self, resolver):
        """Every tool (kernel + discoverable) should have a non-empty description."""
        tools = resolver.get_tool_definitions_sync()
        assert len(tools) >= 11, f"Expected at least 11 tools, got {len(tools)}"
        for t in tools:
            assert t.description, f"Tool {t.name} has empty description"

    def test_skills_have_descriptions(self, resolver):
        """Skills from filesystem should have non-empty descriptions."""
        skills = resolver.get_skill_definitions_sync()
        assert len(skills) > 0, "No skills found"
        for s in skills:
            assert s.description, f"Skill {s.name} has empty description"

    def test_merge_preserves_filesystem_descriptions(self, resolver):
        """Agent.yaml overrides with only name+activation must not clear descriptions."""
        tools = resolver.get_tool_definitions_sync()
        # file_writer has description in tool.yaml
        writer = next(t for t in tools if t.name == "file_writer")
        assert writer.description, "file_writer should have description from tool.yaml"
        assert "Create" in writer.description or "file" in writer.description.lower()

    def test_discoverable_tools_exist(self, resolver):
        """Discoverable tools should be in the list with descriptions."""
        tools = resolver.get_tool_definitions_sync()
        discoverable = [t for t in tools if t.activation == "discoverable"]
        assert len(discoverable) >= 5, f"Expected >=5 discoverable tools, got {len(discoverable)}"
        for t in discoverable:
            assert t.description, f"Discoverable tool {t.name} has empty description"
            assert t.parameters, f"Discoverable tool {t.name} has empty parameters"


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
    """Verify system prompt inventory includes all expected sections.

    Mirrors BaseAgent's pattern: merge filesystem data via the resolver first,
    then feed back into config.tools/config.skills before building sections.
    """

    _sections = None

    @pytest.fixture(autouse=True)
    def _build_inventory(self, resolver, agent_config):
        """Mirrors BaseAgent.__init__ merge at lines 196-215."""
        merged_specs = resolver.get_tool_definitions_sync()
        if merged_specs:
            from arf.core.config_base import ToolConfig as _ToolConfig
            agent_tool_activations = {
                t.name: t.activation for t in (agent_config.tools or [])
            }
            merged_tools = []
            for td in merged_specs:
                d = td if isinstance(td, dict) else td.model_dump()
                name = d.get("name", "")
                activation = agent_tool_activations.get(
                    name, d.get("activation", "discoverable")
                )
                d["activation"] = activation
                merged_tools.append(_ToolConfig(**d))
            agent_config.tools = merged_tools

        merged_skills = resolver.get_skill_definitions_sync()
        if merged_skills:
            agent_config.skills = merged_skills

        from arf.agent.base import _build_prompt_sections
        self._sections = _build_prompt_sections(agent_config)

    def test_inventory_contains_available_tools(self):
        """System prompt should contain Available Tools section."""
        assert "Available Tools" in self._sections["inventory"], (
            "Missing Available Tools section"
        )

    def test_inventory_contains_discoverable_tools_section(self):
        """System prompt should contain Discoverable Tools section."""
        assert "Discoverable Tools" in self._sections["inventory"], (
            "Missing Discoverable Tools section"
        )

    def test_inventory_contains_available_skills(self):
        """System prompt should contain Available Skills section."""
        assert "Available Skills" in self._sections["inventory"], (
            "Missing Available Skills section"
        )

    def test_inventory_tool_descriptions_are_present(self):
        """Tool descriptions from tool.yaml should appear in the inventory."""
        assert "- `file_writer`:" in self._sections["inventory"]
        assert "Create" in self._sections["inventory"] or "file" in self._sections["inventory"].lower()

    def test_inventory_skill_descriptions_are_present(self):
        """Skill descriptions from skills/*.yaml should appear in the inventory."""
        assert "code_review" in self._sections["inventory"]
        assert "debug" in self._sections["inventory"]
