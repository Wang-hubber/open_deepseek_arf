"""DefaultGuardRunner — orchestrates input, output, tool guards and permissions."""
from __future__ import annotations

from arf.core.results import GuardResult
from arf.guardrails.none_guard import NoneInputGuard
from arf.guardrails.regex_clean import RegexOutputGuard
from arf.guardrails.path_check import PathCheckToolGuard
from arf.guardrails.permissions import ToolPermissionChecker


class DefaultGuardRunner:
    """Unified guard + permission interface for the engine.

    Tool execution path:
        1. check_tool_params() — path sandbox (hard block)
        2. check_tool_permission() — deny/ask/allow (config-driven)
        3. If 'ask' → engine yields to approval channel (not yet implemented)
        4. If 'deny' → tool call blocked
    """

    def __init__(self, input_guard=None, output_guard=None, tool_guard=None,
                 permission_checker: ToolPermissionChecker | None = None,
                 output_patterns: list[tuple[str, str]] | None = None) -> None:
        self._input = input_guard or NoneInputGuard()
        if output_guard is not None:
            self._output = output_guard
        elif output_patterns is not None:
            self._output = RegexOutputGuard(patterns=output_patterns)
        else:
            self._output = RegexOutputGuard()
        self._tool = tool_guard or PathCheckToolGuard()
        self._permissions = permission_checker or ToolPermissionChecker()

    async def check_input(self, message: str, context: dict) -> GuardResult:
        return await self._input.check(message, context)

    async def check_output(self, message: str, context: dict) -> GuardResult:
        return await self._output.check(message, context)

    async def check_tool_params(self, tool_name: str, params: dict) -> GuardResult:
        """Hard guard: path sandbox, command injection, etc."""
        return await self._tool.check(tool_name, params)

    def check_tool_permission(self, tool_name: str, params: dict) -> str:
        """Soft guard: deny/ask/allow based on config rules."""
        return self._permissions.check(tool_name, params)
