"""SessionModeManager — resolves effective permission mode.

Combines global session_mode with per-agent policy to produce the
effective mode used by the engine.
"""

from __future__ import annotations

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
        return SessionMode(agent_policy.value)


def has_side_effect(tool_name: str, params: dict | None = None) -> bool:
    """Return True if the tool is known to have side effects.

    Used in PLAN mode to block write/exec tools.
    Read-only tools: file_reader, glob, grep, web_search, web_fetch,
                     memory_store (append-only), resource_loader, planner,
                     todo, handoff, model_switch, undo
    Side-effect tools: file_writer, file_deleter, file_download,
                       python_exec, bash, resource_registrar, resource_scaffold,
                       md2pdf, any tool starting with 'mcp__' (unknown)
    """
    READONLY = {
        "file_reader", "glob", "grep", "web_search", "web_fetch",
        "memory_store", "memory_extract", "resource_loader",
        "planner", "todo", "handoff", "model_switch", "undo",
    }
    if tool_name in READONLY:
        return False
    WRITE_TOOLS = {
        "file_writer", "file_deleter", "file_download",
        "python_exec", "bash", "resource_registrar", "resource_scaffold",
        "md2pdf",
    }
    if tool_name in WRITE_TOOLS:
        return True
    # Unknown tools: assume side effect (safe default)
    return True
