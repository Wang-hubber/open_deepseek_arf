"""Protocols for human-in-the-loop domain."""
from typing import Protocol
from arf.core.state import TurnContext
from arf.core.results import ApprovalRequest, ApprovalResponse


class ApprovalPoint(Protocol):
    def should_pause(self, context: TurnContext) -> bool: ...
    def approval_form(self, context: TurnContext) -> ApprovalRequest: ...


class ApprovalChannel(Protocol):
    async def send(self, request: ApprovalRequest) -> str: ...
    async def wait(self, approval_id: str, timeout: int) -> ApprovalResponse: ...
