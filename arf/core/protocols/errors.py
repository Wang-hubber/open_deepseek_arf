"""Protocols for error handling domain."""
from typing import Protocol
from arf.core.state import TurnContext
from arf.core.results import ErrorAction, GuardResult


class ErrorPolicy(Protocol):
    def on_tool_error(self, error: Exception, tool_name: str, attempt: int) -> ErrorAction: ...
    def on_model_error(self, error: Exception, model_name: str, attempt: int) -> ErrorAction: ...
    def on_guardrail_block(self, result: GuardResult, context: TurnContext) -> ErrorAction: ...
