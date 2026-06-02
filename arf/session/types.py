"""Session mode and agent policy types for the unified permission system."""

from __future__ import annotations

from enum import Enum


class SessionMode(str, Enum):
    """Session-level global permission mode.

    auto:  all tools execute directly (ignore agent config)
    ask:   agent policy + deny/ask/allow lists take effect
    plan:  read-only — all side-effect tools denied
    """
    AUTO = "auto"
    ASK = "ask"
    PLAN = "plan"


class AgentPolicy(str, Enum):
    """Per-agent policy override within ask session mode.

    auto:  this agent always allowed (regardless of lists)
    ask:   check deny/ask/allow lists
    plan:  this agent read-only (no side effects)

    When not configured (None), falls through to the global session mode.
    """
    AUTO = "auto"
    ASK = "ask"
    PLAN = "plan"
