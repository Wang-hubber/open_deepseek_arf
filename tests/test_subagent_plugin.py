"""Integration tests for the subagent plugin."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arf.engine.control_plane import ControlPlane
from arf.engine.checkpoint import InMemoryStateStore
from arf.event_bus import InMemoryEventBus
from arf.resources.resolver import ResourceResolver
from arf.resources.providers.tool_provider import ToolProvider
from arf.engine.tool_executor import ConcurrentToolExecutor
from arf.errors.retry import DefaultErrorPolicy
from arf.engine.loop_strategies.react import ReActStrategy
from arf.plugins.subagent.tools.subagent.function import (
    execute, FilteredToolProvider,
)


@pytest.fixture
def app_tools_dir(tmp_path):
    """Create a temporary tools directory with read + grep + glob + bash + file_writer tools."""
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    for name in ["read", "grep", "glob", "bash", "file_writer"]:
        tool_dir = tools_dir / name
        tool_dir.mkdir()
        (tool_dir / "tool.yaml").write_text(
            f"name: {name}\ndescription: Test tool {name}\n"
            "parameters:\n  type: object\n  properties: {}\nactivation: kernel\n"
        )
        (tool_dir / "function.py").write_text(
            "async def execute(**kwargs):\n"
            f"    return {{'ok': True, 'tool': '{name}'}}\n"
        )
    return str(tools_dir)


class TestSubagentExecute:
    def test_returns_summary(self, app_tools_dir):
        """Subagent runs with a fake call_model and returns the summary."""
        fake_response = MagicMock()
        fake_response.content = "This project uses pytest for testing."
        fake_response.tool_calls = None
        fake_response.usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

        async def fake_call_model(messages, model_name="", tools=None):
            return {
                "content": fake_response.content,
                "tool_calls": [],
                "usage": fake_response.usage,
                "reasoning": "",
                "finish_reason": "stop",
            }

        tool_provider = ToolProvider(app_tools_dir)
        resolver = ResourceResolver(tool_provider=tool_provider)
        executor = ConcurrentToolExecutor(tool_resolver=resolver)
        state_store = InMemoryStateStore()

        engine = ControlPlane(
            loop_strategy=ReActStrategy(max_turns=10),
            state_store=state_store,
            tool_executor=executor,
            event_bus=InMemoryEventBus(),
            call_model=fake_call_model,
            workspace_dir="/tmp/test",
            mcp_tool_resolver=None,
        )
        engine.tool_resolver = resolver  # For subagent FilteredToolProvider access

        async def _run():
            return await execute(
                prompt="What test framework does this project use?",
                model="quick",
                description="test-framework-check",
                _engine=engine,
                _state_store=state_store,
                _workspace="/tmp/test",
            )

        result = asyncio.run(_run())
        assert result["content"] == "This project uses pytest for testing."
        assert result["model"] == "quick"
        assert "error" not in result

    def test_empty_prompt_returns_error(self):
        """Empty prompt returns error immediately without engine invocation."""
        async def _run():
            return await execute(
                prompt="",
                _engine=MagicMock(),
                _state_store=MagicMock(),
            )

        result = asyncio.run(_run())
        assert result["error"] is True
        assert "required" in result["content"]

    def test_subagent_context_isolation(self, app_tools_dir):
        """Subagent messages start with only the prompt, not parent history."""
        fake_response = MagicMock()
        fake_response.content = "done"
        fake_response.tool_calls = None
        fake_response.usage = {}

        call_messages = []

        async def fake_call_model(messages, model_name="", tools=None):
            call_messages.append(list(messages))
            return {
                "content": fake_response.content,
                "tool_calls": [],
                "usage": {},
                "reasoning": "",
                "finish_reason": "stop",
            }

        tool_provider = ToolProvider(app_tools_dir)
        resolver = ResourceResolver(tool_provider=tool_provider)
        executor = ConcurrentToolExecutor(tool_resolver=resolver)
        state_store = InMemoryStateStore()

        engine = ControlPlane(
            loop_strategy=ReActStrategy(max_turns=10),
            state_store=state_store,
            tool_executor=executor,
            event_bus=InMemoryEventBus(),
            call_model=fake_call_model,
            workspace_dir="/tmp/test",
            mcp_tool_resolver=None,
        )
        engine.tool_resolver = resolver  # For subagent FilteredToolProvider access

        async def _run():
            return await execute(
                prompt="summarize the project",
                description="check",
                _engine=engine,
                _state_store=state_store,
                _workspace="/tmp/test",
            )

        asyncio.run(_run())
        # Subagent should have exactly the user prompt (no parent history leaked)
        assert len(call_messages) == 1
        # Engine prepends an empty system prompt; the key check is that the
        # user message is the subagent prompt, not any parent conversation.
        assert len(call_messages[0]) == 2
        assert call_messages[0][0]["role"] == "system"
        assert call_messages[0][1]["role"] == "user"
        assert call_messages[0][1]["content"] == "summarize the project"

    def test_error_graceful_on_invalid_model(self, app_tools_dir):
        """Subagent returns error content when call_model raises."""
        async def fake_call_model(messages, model_name="", tools=None):
            raise RuntimeError("Model not found: invalid-model")

        tool_provider = ToolProvider(app_tools_dir)
        resolver = ResourceResolver(tool_provider=tool_provider)
        executor = ConcurrentToolExecutor(tool_resolver=resolver)
        state_store = InMemoryStateStore()

        engine = ControlPlane(
            loop_strategy=ReActStrategy(max_turns=10),
            state_store=state_store,
            tool_executor=executor,
            event_bus=InMemoryEventBus(),
            call_model=fake_call_model,
            workspace_dir="/tmp/test",
            mcp_tool_resolver=None,
        )
        engine.tool_resolver = resolver  # For subagent FilteredToolProvider access

        async def _run():
            return await execute(
                prompt="do something",
                model="invalid-model",
                _engine=engine,
                _state_store=state_store,
                _workspace="/tmp/test",
            )

        result = asyncio.run(_run())
        assert result["error"] is True
        assert "Model not found" in result["content"]


class TestFilteredToolProvider:
    def test_filters_tools(self, app_tools_dir):
        """FilteredToolProvider only lists allowed tools."""
        parent = ToolProvider(app_tools_dir)
        filtered = FilteredToolProvider(parent, {"read", "bash"})
        tools = filtered.list()
        names = {t.name for t in tools}
        assert names == {"read", "bash"}

    def test_blocks_execution_of_disallowed_tool(self, app_tools_dir):
        """FilteredToolProvider blocks execution of tools not in allowed set."""
        parent = ToolProvider(app_tools_dir)
        filtered = FilteredToolProvider(parent, {"read"})

        async def _run():
            return await filtered.execute("bash", {})

        with pytest.raises(ValueError, match="not allowed"):
            asyncio.run(_run())
