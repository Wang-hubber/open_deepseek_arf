"""Protocols for guardrails domain."""
from typing import Protocol
from arf.core.results import GuardResult


class GuardRunner(Protocol):
    """Engine's single interface to guardrails."""
    async def check_input(self, message: str, context: dict) -> GuardResult: ...
    async def check_output(self, message: str, context: dict) -> GuardResult: ...
    async def check_tool_params(self, tool_name: str, params: dict) -> GuardResult: ...


class InputGuardrail(Protocol):
    async def check(self, message: str, context: dict) -> GuardResult: ...


class OutputGuardrail(Protocol):
    async def check(self, message: str, context: dict) -> GuardResult: ...


class ToolGuardrail(Protocol):
    async def check(self, tool_name: str, params: dict) -> GuardResult: ...
