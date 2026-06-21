"""Tests for harness snapshot -- config capture, hash, diff on resume."""
import json
import tempfile
from pathlib import Path

import pytest

from arf.agent.state import ModelResult
from arf.agent.primitive import PrimitiveAgent
from arf.harness.engine import AgentHarness
from arf.event_bus import InMemoryEventBus
from arf.core.config_base import ToolConfig


class FakeToolProvider:
    def list(self):
        return [
            ToolConfig(name="read_file", description="Read a file",
                       parameters={"path": {"type": "string"}}),
        ]


class FakePlugin:
    """Minimal plugin for snapshot collection."""
    name = "test_plugin"
    config = {"threshold": 100}


class FakeResourceResolver:
    def __init__(self):
        self._tool_provider = FakeToolProvider()

    def list_tools(self):
        tools = self._tool_provider.list()
        return {t.name: {"description": t.description, "parameters": t.parameters} for t in tools}

    def list_skills(self):
        return {}


def make_agent(call_model):
    agent = PrimitiveAgent(
        agent_id="snap1",
        model_config={"api_base": "", "api_key_env": "", "model_name": "test-model", "context_window": 0},
        call_model=call_model,
    )
    # Attach model adapter with describe() for snapshot
    from arf.core.model_adapter import ModelAdapter
    agent._model_adapter = ModelAdapter({
        "base_url": "http://localhost",
        "model_name": "test-model",
        "temperature": 0.5,
    })
    return agent


class TestHarnessSnapshotBuild:
    @pytest.mark.anyio
    async def test_snapshot_built_and_persisted(self, tmp_path):
        """After a run, state file contains a snapshot with hash + config."""
        data_dir = str(tmp_path)

        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[], tool_manager=None, data_dir=data_dir)
        events = [e async for e in harness.run("hi")]

        session_id = agent.state.session_id
        state_file = Path(data_dir) / session_id / "state" / f"{session_id}.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert "snapshot" in state
        snap = state["snapshot"]
        assert "hash" in snap
        assert len(snap["hash"]) == 12
        assert "config" in snap
        assert "model" in snap["config"]
        assert "system_prompt" in snap["config"]

    @pytest.mark.anyio
    async def test_snapshot_hash_stable_for_same_config(self, tmp_path):
        """Two runs with identical config produce the same snapshot hash."""
        data_dir1 = str(tmp_path / "run1")
        data_dir2 = str(tmp_path / "run2")

        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        agent1 = make_agent(fake_call)
        h1 = AgentHarness(agent1, plugins=[], tool_manager=None, data_dir=data_dir1)
        [e async for e in h1.run("a")]

        agent2 = make_agent(fake_call)
        h2 = AgentHarness(agent2, plugins=[], tool_manager=None, data_dir=data_dir2)
        [e async for e in h2.run("b")]

        snap1 = agent1.state.snapshot
        snap2 = agent2.state.snapshot
        assert snap1["hash"] == snap2["hash"]

    @pytest.mark.anyio
    async def test_config_mismatch_emitted_on_hash_change(self, tmp_path):
        """Resuming a session with different config emits config_mismatch."""
        data_dir = str(tmp_path)

        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        # First run -- create session with first config
        agent1 = make_agent(fake_call)
        h1 = AgentHarness(agent1, plugins=[], tool_manager=None, data_dir=data_dir)
        [e async for e in h1.run("hi")]
        session_id = agent1.state.session_id

        # Modify the persisted state's snapshot hash to simulate config change
        state_file = Path(data_dir) / session_id / "state" / f"{session_id}.json"
        state = json.loads(state_file.read_text())
        state["snapshot"]["hash"] = "deadbeef0000"  # tamper
        state_file.write_text(json.dumps(state))

        # Second run -- same session_id, different config -> should mismatch
        agent2 = make_agent(fake_call)
        h2 = AgentHarness(agent2, plugins=[], tool_manager=None, data_dir=data_dir)
        events = [e async for e in h2.run("resume", session_id=session_id)]

        mismatch_events = [e for e in events if e.type == "config_mismatch"]
        assert len(mismatch_events) == 1
        assert mismatch_events[0].data["old_hash"] == "deadbeef0000"
        assert "new_hash" in mismatch_events[0].data

    @pytest.mark.anyio
    async def test_new_session_emits_nothing(self, tmp_path):
        """New session does not emit config_mismatch."""
        data_dir = str(tmp_path)

        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[], tool_manager=None, data_dir=data_dir)
        events = [e async for e in harness.run("hi")]

        mismatch_events = [e for e in events if e.type == "config_mismatch"]
        assert len(mismatch_events) == 0
