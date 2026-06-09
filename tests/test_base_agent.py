"""Characterization tests for BaseAgent initialization."""
from pathlib import Path

import pytest

from arf.agent.config import AgentConfig
from arf.agent.base import BaseAgent


@pytest.fixture
def temp_agent_dir(tmp_path):
    """Create minimal agent directory structure for testing."""
    tools_dir = tmp_path / "tools"
    skills_dir = tmp_path / "skills"
    models_dir = tmp_path / "models"
    for d in [tools_dir, skills_dir, models_dir]:
        d.mkdir()
    return tmp_path


@pytest.fixture
def minimal_config(temp_agent_dir):
    """Minimal AgentConfig for testing defaults."""
    return AgentConfig(
        name="test_agent",
        description="Test agent",
    )


class TestBaseAgentInitialization:
    @pytest.mark.anyio
    async def test_constructs_with_minimal_config(self, minimal_config, temp_agent_dir, monkeypatch):
        """Constructing with minimal config must succeed."""
        monkeypatch.chdir(temp_agent_dir)
        agent = BaseAgent(minimal_config)
        assert agent.config.name == "test_agent"
        assert agent.engine is not None
        assert agent.event_bus is not None
        assert agent.state_store is not None
        assert agent.tool_resolver is not None

    @pytest.mark.anyio
    async def test_override_protocols_work(self, minimal_config, temp_agent_dir, monkeypatch):
        """override_protocols must allow injection of custom implementations."""
        from arf.engine.checkpoint import InMemoryStateStore

        monkeypatch.chdir(temp_agent_dir)
        agent = BaseAgent(
            minimal_config,
            state_store=InMemoryStateStore(),
        )
        from arf.engine.checkpoint import FileStateStore
        assert not isinstance(agent.state_store, FileStateStore)

    @pytest.mark.anyio
    async def test_legacy_transaction_ctx_absorbed(self, minimal_config, temp_agent_dir, monkeypatch):
        """Legacy transaction_ctx override must be silently absorbed, not leaked."""
        monkeypatch.chdir(temp_agent_dir)
        agent = BaseAgent(minimal_config, transaction_ctx=None)
        assert agent.engine is not None

    @pytest.mark.anyio
    async def test_engine_has_call_model(self, minimal_config, temp_agent_dir, monkeypatch):
        """Engine must have _call_model injected."""
        monkeypatch.chdir(temp_agent_dir)
        agent = BaseAgent(minimal_config)
        assert agent.engine._call_model is not None
        assert agent.engine.state_store is agent.state_store
