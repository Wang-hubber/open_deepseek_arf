"""Promotion gate — thin compatibility wrapper.

Delegates to the unified arf.session system.
Kept for existing code that references Promotion; will be removed in a future cleanup.
"""
from __future__ import annotations

from typing import Any
from arf.core.execution import Decision, Executable
from arf.session import PermissionLists, PermissionRegistry


class Promotion:
    """Deprecated — use SessionModeManager + PermissionRegistry directly."""

    def __init__(self, strategy: str = "ask", **kwargs: Any) -> None:
        self._strategy_name = strategy
        self._registry = PermissionRegistry()
        self._lists = PermissionLists(
            deny=set(kwargs.get("deny", [])),
            ask=set(kwargs.get("ask", [])),
            allow=set(kwargs.get("allow", [])),
            deny_patterns=kwargs.get("deny_patterns", []),
        )

    def evaluate(self, executable: Executable, **context: Any) -> Decision:
        params = context.get("params", {})
        result = self._registry.evaluate(executable.name, params, self._lists)
        return Decision(action=result.action, reason=result.reason)

    def reconfigure(self, *, deny=None, ask=None, allow=None, deny_patterns=None) -> None:
        if deny is not None:
            self._lists.deny = set(deny)
        if ask is not None:
            self._lists.ask = set(ask)
        if allow is not None:
            self._lists.allow = set(allow)
        if deny_patterns is not None:
            self._lists.deny_patterns = list(deny_patterns)
