"""SessionModeManager — resolves effective permission mode.

Combines global session_mode with per-agent policy to produce the
effective mode used by the engine.
"""

from __future__ import annotations

from typing import Any
from arf.session.types import AgentPolicy, SessionMode


class SessionModeManager:
    """Resolve effective mode from global session mode + agent policy.

    Combination rules:
    ┌──────────────────┬────────────────┬──────────────┐
    │ global           │ agent policy   │ effective    │
    ├──────────────────┼────────────────┼──────────────┤
    │ auto             │ any / None     │ auto         │
    │ plan             │ any / None     │ plan         │
    │ ask              │ auto           │ auto         │
    │ ask              │ ask            │ ask          │
    │ ask              │ plan           │ plan         │
    │ ask              │ None           │ ask          │
    └──────────────────┴────────────────┴──────────────┘

    auto and plan are hard overrides — they ignore agent policy entirely.
    """

    def __init__(self, global_mode: SessionMode = SessionMode.ASK) -> None:
        self._global = global_mode

    @property
    def global_mode(self) -> SessionMode:
        return self._global

    def set_global(self, mode: SessionMode) -> None:
        """Switch global session mode at runtime."""
        self._global = mode

    def resolve(self, agent_policy: AgentPolicy | None) -> SessionMode:
        """Return the effective mode for a given agent.

        agent_policy=None means "follow global".
        """
        if self._global in (SessionMode.AUTO, SessionMode.PLAN):
            return self._global

        # global is ASK — agent policy takes effect
        if agent_policy is None:
            return SessionMode.ASK  # follow global
        # Explicit mapping avoids fragile implicit conversion
        return {
            AgentPolicy.AUTO: SessionMode.AUTO,
            AgentPolicy.ASK: SessionMode.ASK,
            AgentPolicy.PLAN: SessionMode.PLAN,
        }[agent_policy]


def has_side_effect(tool_name: str, tool_annotations: dict[str, Any] | None = None) -> bool:
    """Return True if the tool is known to have side effects.

    Resolution order:
    1. tool_annotations[name][\"readOnlyHint\"] — tool's own declaration (authoritative)
    2. Hardcoded fallback sets (bare names only, for kernel tools without tool.yaml)
    3. Unknown tools: assume side effect (safe default)

    Handles namespaced names: ``user__write_file`` → bare ``write_file`` for lookup.
    """
    from arf.core.tool_naming import split_name
    namespace, bare = split_name(tool_name)

    # 1. Check tool annotations (both full name and bare name)
    if tool_annotations:
        for lookup in (tool_name, bare):
            ann = tool_annotations.get(lookup)
            if ann and "readOnlyHint" in ann:
                if ann["readOnlyHint"] is True:
                    return False
                if ann["readOnlyHint"] is False:
                    return True

    # 2. Hardcoded fallback — bare name lookup
    # Only kernel tools — user/plugin tools declare readOnlyHint in tool.yaml
    READ_ONLY = {
        "ask_user", "list_secrets", "read_secret",
        "search_task_memory", "use_skill",
    }
    if bare in READ_ONLY:
        return False
    WRITE_TOOLS = {
        "task_complete",
        "write_project_memory", "write_secret", "write_user_memory",
    }
    if bare in WRITE_TOOLS:
        return True

    # 3. Unknown: assume side effect (safe default)
    return True
