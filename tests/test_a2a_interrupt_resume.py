"""Tests for A2A interrupt/resume feature — child_tasks field."""
import pytest
from arf.core.state import AgentState


def test_agent_state_accepts_child_tasks():
    """AgentState should accept child_tasks list of task dicts."""
    state: AgentState = {
        "session_id": "parent_s1",
        "messages": [],
        "child_tasks": [
            {
                "task_id": "task_1",
                "child_session_id": "s1--task_1",
                "agent_name": "coder",
                "status": "running",
                "created_at": 1718745600.0,
            }
        ],
    }
    assert state["child_tasks"][0]["task_id"] == "task_1"
    assert state["child_tasks"][0]["status"] == "running"


def test_a2a_config_child_resume_default():
    """child_resume defaults to 'auto'."""
    from arf.plugins.a2a.config import A2APluginConfig

    cfg = A2APluginConfig()
    assert cfg.child_resume == "auto"


def test_a2a_config_child_resume_notify():
    """child_resume can be set to 'notify'."""
    from arf.plugins.a2a.config import A2APluginConfig

    cfg = A2APluginConfig(child_resume="notify")
    assert cfg.child_resume == "notify"


def test_a2a_config_child_resume_invalid():
    """child_resume rejects invalid values."""
    from arf.plugins.a2a.config import A2APluginConfig

    with pytest.raises(Exception):
        A2APluginConfig(child_resume="invalid")
