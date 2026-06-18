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
