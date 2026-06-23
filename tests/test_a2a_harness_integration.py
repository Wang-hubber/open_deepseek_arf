"""Smoke tests for A2A plugin wired through AgentHarness."""
import pytest

from arf.communication.queued_delegator import QueuedTaskDelegator
from arf.plugins.a2a_subagents.tools import _registry as a2a_registry


class TestA2AHarnessIntegration:
    """Verify plugin initializes correctly within a harness run."""

    @pytest.fixture(autouse=True)
    def setup_registry(self):
        a2a_registry.delegator = None
        a2a_registry.parent_config = None
        a2a_registry.current_session_id = ""
        yield
        a2a_registry.delegator = None
        a2a_registry.parent_config = None

    @pytest.mark.anyio
    async def test_plugin_initializes_via_loader(self):
        """Plugin class is discoverable by instantiate_plugins."""
        from arf.harness.loader import instantiate_plugins

        configs = [{
            "name": "a2a_subagents",
            "events": [
                {"hook_name": "session_start", "event_name": "init", "mode": "side"},
            ],
            "config": {"max_concurrent_tasks": 2},
        }]

        plugins = instantiate_plugins(configs)
        assert len(plugins) == 1
        assert plugins[0].name == "a2a_subagents"
        assert a2a_registry.delegator is not None
        assert isinstance(a2a_registry.delegator, QueuedTaskDelegator)

    @pytest.mark.anyio
    async def test_delegate_task_fails_gracefully_without_harness(self):
        """delegate_task returns error when no parent_config (no session_start)."""
        from arf.plugins.a2a_subagents.tools.delegate_task.function import execute

        # Initialize delegator only (no parent_config)
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=2)
        a2a_registry.max_task_timeout = 600.0
        a2a_registry.current_session_id = "test_sid"

        result = await execute(task="test task", agent="", session_id="test_sid")
        assert result["ok"] is False
        assert "parent config not captured" in result.get("error", "")
