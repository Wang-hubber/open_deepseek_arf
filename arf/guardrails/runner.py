"""DefaultGuardRunner — orchestrates input, output, tool guards and permissions."""
from __future__ import annotations

from arf.core.results import GuardResult
from arf.guardrails.none_guard import NoneInputGuard
from arf.guardrails.regex_clean import RegexOutputGuard
from arf.session import PermissionLists, PermissionRegistry


class DefaultGuardRunner:
    """Unified guard + permission interface for the engine.

    Tool execution path:
        1. Engine resolves effective mode via SessionModeManager
        2. If ASK mode → PermissionRegistry evaluates deny/ask/allow
        3. PathCheckToolGuard runs in executor (not here), as pre-execution check
    """

    def __init__(self, input_guard=None, output_guard=None, tool_guard=None,
                 permission_registry: PermissionRegistry | None = None,
                 permission_lists: PermissionLists | None = None,
                 output_patterns: list[tuple[str, str]] | None = None,
                 content_guard=None) -> None:
        self._input = input_guard or NoneInputGuard()
        if output_guard is not None:
            self._output = output_guard
        elif output_patterns is not None:
            self._output = RegexOutputGuard(patterns=output_patterns)
        else:
            self._output = RegexOutputGuard()
        self._tool = tool_guard
        self._permission_registry = permission_registry or PermissionRegistry()
        self._permission_lists = permission_lists or PermissionLists()
        self._content_guard = content_guard

    async def check_input(self, message: str, context: dict) -> GuardResult:
        return await self._input.check(message, context)

    async def check_output(self, message: str, context: dict) -> GuardResult:
        return await self._output.check(message, context)

    async def check_tool_params(
        self, tool_name: str, params: dict, boundary=None
    ) -> GuardResult:
        """Hard guard: path sandbox, command injection, etc.

        Called from tool executor as pre-execution check, NOT from
        the engine permission pipeline. boundary is resolved per-tool
        by the executor.
        """
        if self._tool is not None and boundary is not None:
            return await self._tool.check(tool_name, params, boundary)
        return GuardResult(allowed=True)

    def check_tool_permission(self, tool_name: str, params: dict) -> str:
        """Soft guard: deny/ask/allow based on permission lists."""
        result = self._permission_registry.evaluate(tool_name, params, self._permission_lists)
        return result.action

    def swap_lists(self, lists: PermissionLists) -> None:
        """Hot-swap permission lists at runtime (e.g. during agent handoff)."""
        self._permission_lists = lists
