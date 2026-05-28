"""Tests for memory extraction redesign — round hooks, PluginProvider, resident memory."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


def _build_engine(**overrides):
    """Build a minimal GraphEngine with mock dependencies."""
    from arf.engine.graph import GraphEngine

    defaults = {
        "loop_strategy": MagicMock(),
        "state_store": MagicMock(),
        "tool_executor": MagicMock(),
        "tool_resolver": MagicMock(),
        "error_policy": None,
        "model_router": None,
        "event_bus": None,
        "system_prompt": "",
    }
    defaults.update(overrides)
    return GraphEngine(**defaults)


class TestRoundHooks:
    """round_start / round_end hook events.

    round_start fires in BaseAgent (tested at integration level).
    round_end fires in GraphEngine (tested below).
    """

    def _state(self):
        return {
            "session_id": "test",
            "agent_name": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "current_model": "test",
            "current_turn": 0,
            "interaction_round": 0,
            "context_summary": "",
            "tool_results": {},
            "plan": None,
            "metadata": {},
        }

    def _make_engine(self, hook_runner):
        """Build engine with mocks that survive the full invoke() cycle."""
        loop_strategy = MagicMock()
        loop_strategy.should_continue.side_effect = [True, False]

        state_store = MagicMock()
        state_store.get = AsyncMock(return_value=None)
        state_store.put = AsyncMock()

        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        engine = _build_engine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            hook_runner=hook_runner,
            tool_resolver=tool_resolver,
        )
        engine._call_model = AsyncMock(return_value={
            "content": "hello", "tool_calls": [], "usage": {"total_tokens": 10},
        })
        return engine

    def test_round_end_hook_fired_in_engine(self):
        """round_end hook fires in invoke() after while loop ends."""
        hook_runner = MagicMock()
        hook_runner.fire = AsyncMock(return_value=[])

        engine = self._make_engine(hook_runner)
        asyncio.run(engine.invoke(self._state()))

        round_end_calls = [
            c for c in hook_runner.fire.call_args_list
            if c[0][0] == "round_end"
        ]
        assert len(round_end_calls) == 1, "round_end hook should fire once"

    def test_round_end_fires_before_session_end(self):
        """round_end hook fires after loop ends, before session_end."""
        call_order = []
        hook_runner = MagicMock()

        async def track_fire(event_type, context):
            call_order.append(event_type)
            return []
        hook_runner.fire = track_fire

        engine = self._make_engine(hook_runner)
        asyncio.run(engine.invoke(self._state()))

        assert "round_end" in call_order, f"Expected round_end in call_order: {call_order}"
        assert "session_end" in call_order, f"Expected session_end in call_order: {call_order}"
        re_idx = call_order.index("round_end")
        se_idx = call_order.index("session_end")
        assert re_idx < se_idx, (
            f"round_end (idx={re_idx}) must fire before session_end (idx={se_idx})"
        )


class TestPluginProviderHooks:
    """PluginProvider hooks/ scanning."""

    @pytest.fixture
    def plugins_root(self, tmp_path):
        """Create temp plugins dir with a memory plugin that has hooks/."""
        root = tmp_path / "plugins"
        root.mkdir()

        # memory plugin with hooks
        memory = root / "memory" / "hooks"
        memory.mkdir(parents=True)
        (memory / "round_end.py").write_text(
            "import os, sys\nsys.exit(0)\n", encoding="utf-8"
        )

        # memory plugin also has tools/
        mem_tool = root / "memory" / "tools" / "memory_extract"
        mem_tool.mkdir(parents=True)
        (mem_tool / "tool.yaml").write_text(yaml.dump({
            "name": "memory_extract",
            "description": "Extract memories",
            "parameters": {"type": "object", "properties": {}},
        }), encoding="utf-8")

        return root

    def test_scans_hooks_directory(self, plugins_root):
        """PluginProvider scans hooks/ and generates HookDefinitions."""
        from arf.resources.providers.plugin_provider import PluginProvider

        provider = PluginProvider(plugins_root, enabled=["memory"])
        hooks = provider.list_hooks()

        assert len(hooks) >= 1, f"Expected at least 1 hook, got {len(hooks)}"
        round_end_hooks = [h for h in hooks if h.type == "round_end"]
        assert len(round_end_hooks) == 1
        assert round_end_hooks[0].name == "memory__round_end"

    def test_plugin_config_passed_as_env(self, plugins_root):
        """Plugin config is serialized into hook env as ARF_PLUGIN_CONFIG."""
        from arf.resources.providers.plugin_provider import PluginProvider

        provider = PluginProvider(plugins_root, enabled=["memory"])
        hooks = provider.list_hooks()

        round_end_hook = [h for h in hooks if h.type == "round_end"][0]
        assert round_end_hook.env is not None
        assert "ARF_PLUGIN_CONFIG" in round_end_hook.env
        config = json.loads(round_end_hook.env["ARF_PLUGIN_CONFIG"])
        assert config["plugin_name"] == "memory"

    def test_no_hooks_dir_no_error(self, plugins_root):
        """Plugin without hooks/ directory should not error."""
        from arf.resources.providers.plugin_provider import PluginProvider

        # Add a plugin without hooks
        other = plugins_root / "other" / "tools" / "other_tool"
        other.mkdir(parents=True)
        (other / "tool.yaml").write_text(yaml.dump({
            "name": "other_tool",
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        }), encoding="utf-8")

        provider = PluginProvider(plugins_root, enabled=["other"])
        hooks = provider.list_hooks()
        assert hooks == []


class TestMemoryConfig:
    """Resident memory config fields."""

    def test_default_resident_file(self):
        from arf.core.config_base import MemoryConfig
        cfg = MemoryConfig()
        assert cfg.resident_file == "memory.md"

    def test_default_max_size_kb(self):
        from arf.core.config_base import MemoryConfig
        cfg = MemoryConfig()
        assert cfg.max_size_kb == 300

    def test_override_max_size_kb(self):
        from arf.core.config_base import MemoryConfig
        cfg = MemoryConfig(max_size_kb=500)
        assert cfg.max_size_kb == 500


class TestLoadResidentMemory:
    """_load_resident_memory function tests."""

    def test_loads_memory_file(self, tmp_path):
        """Reads memory.md and returns content."""
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("## User Identity\n- Developer", encoding="utf-8")

        from arf.agent.base import _load_resident_memory
        result = _load_resident_memory(str(tmp_path))

        assert "## User Identity" in result
        assert "- Developer" in result

    def test_returns_empty_when_file_missing(self, tmp_path):
        """Missing file returns empty string, no error."""
        from arf.agent.base import _load_resident_memory
        result = _load_resident_memory(str(tmp_path))
        assert result == ""

    def test_truncates_when_over_limit(self, tmp_path):
        """Content over max_size_bytes is truncated."""
        from arf.agent.base import _load_resident_memory

        memory_file = tmp_path / "memory.md"
        content = "x" * 2000
        memory_file.write_text(content, encoding="utf-8")

        result = _load_resident_memory(str(tmp_path), max_size_bytes=500)
        result_bytes = len(result.encode("utf-8"))
        assert result_bytes <= 700  # allow extra for truncation warning
        assert "truncated" in result.lower()

    def test_strips_trailing_incomplete_line(self, tmp_path):
        """Truncation keeps complete lines only."""
        from arf.agent.base import _load_resident_memory

        memory_file = tmp_path / "memory.md"
        # 3 lines, ~20 bytes each → 60 bytes total. Limit to 40 → keep 2 full lines.
        lines = "line one data here\nline two data here\nline three here\n"
        memory_file.write_text(lines, encoding="utf-8")

        result = _load_resident_memory(str(tmp_path), max_size_bytes=40)
        assert "line one" in result
        assert "line two" in result
        assert "line three" not in result
