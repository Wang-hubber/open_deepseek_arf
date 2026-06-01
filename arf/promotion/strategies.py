"""Promotion strategies: AutoStrategy, AskStrategy, PlanStrategy."""

from __future__ import annotations

import re
from typing import Any

from arf.core.execution import Decision, Executable


class AutoStrategy:
    """All executables pass through. For fully-trusted environments."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    def evaluate(self, executable: Executable, **context: Any) -> Decision:
        return Decision(action="allow", reason="auto strategy: all allowed")


class AskStrategy:
    """Three-tier deny > ask > allow evaluation.

    Mirrors the existing ToolPermissionChecker logic migrated from
    arf/guardrails/permissions.py.
    """

    def __init__(
        self,
        deny: list[str] | None = None,
        ask: list[str] | None = None,
        allow: list[str] | None = None,
        deny_patterns: list[str] | None = None,
    ) -> None:
        self.deny_list: set[str] = set(deny or [])
        self.ask_list: set[str] = set(ask or [])
        self.allow_list: set[str] = set(allow or [])
        self._deny_patterns: list[str] = deny_patterns or []

    def evaluate(self, executable: Executable, **context: Any) -> Decision:
        # 1. Check deny patterns against context params
        params = context.get("params", {})
        params_str = str(params)
        for pattern in self._deny_patterns:
            if re.search(pattern, params_str):
                return Decision(
                    action="deny",
                    reason=f"matched deny pattern: {pattern}",
                )

        # 2. Check deny list
        if executable.name in self.deny_list:
            return Decision(
                action="deny",
                reason=f"'{executable.name}' is in deny list",
            )

        # 3. Check ask list
        if executable.name in self.ask_list:
            return Decision(
                action="ask",
                reason=f"'{executable.name}' requires approval",
            )

        # 4. Check allow list
        if executable.name in self.allow_list:
            return Decision(action="allow", reason="'%s' is in allow list" % executable.name)

        # 5. Default: ask for unknown
        return Decision(
            action="ask",
            reason=f"'{executable.name}' is unknown, requires approval",
        )


class PlanStrategy:
    """Read-only mode: side_effect=False -> allow, side_effect=True -> deny.

    Model calls are always allowed regardless of side_effect flag.
    """

    def evaluate(self, executable: Executable, **context: Any) -> Decision:
        if executable.kind == "model_call":
            return Decision(action="allow", reason="model calls are read-only")

        if not executable.side_effect:
            return Decision(action="allow", reason="no side effect")

        return Decision(
            action="deny",
            reason="计划模式仅允许只读操作，该操作会产生副作用",
        )
