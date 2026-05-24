"""Agent registry — thread-safe access to the running agent instance from tools."""
from __future__ import annotations
from typing import Any


_agent: Any = None


def set_agent(agent) -> None:
    global _agent
    _agent = agent


def get_agent():
    return _agent
