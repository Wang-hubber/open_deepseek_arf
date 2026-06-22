"""Protocols for multi-agent communication."""
from __future__ import annotations

import asyncio
from typing import Protocol, AsyncIterator, Callable, Awaitable
from dataclasses import dataclass
from typing import Literal


@dataclass
class AgentMessage:
    sender: str
    receiver: str | None
    type: Literal["task_delegate", "task_request", "task_result", "info", "query", "answer"]
    payload: dict
    priority: Literal["normal", "urgent"] = "normal"
    reply_to: str | None = None
    correlation_id: str = ""

@dataclass
class AgentInfo:
    name: str
    description: str
    capabilities: list[str]


class AgentBus(Protocol):
    async def send(self, message: AgentMessage) -> None: ...
    async def receive(self, agent_name: str) -> AsyncIterator[AgentMessage]: ...
    async def register(self, agent: AgentInfo) -> None: ...
    async def discover(self, capability: str | None = None) -> list[AgentInfo]: ...
    async def wait_for_message(
        self, agent_name: str,
        timeout: float | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> bool: ...


class TaskDelegator(Protocol):
    async def delegate(self, task: dict, from_agent: str, to_agent: str) -> str: ...
    async def get_result(self, handle_id: str, timeout: int) -> dict: ...
    async def dispatch(self, session_id: str, task: dict,
                       runner: Callable[[dict], Awaitable[dict]]) -> dict: ...
    async def complete(self, session_id: str, task_id: str, result: dict) -> None: ...
    async def get_pending(self, session_id: str) -> list[dict]: ...
    async def queue_status(self, session_id: str) -> dict: ...
    async def cancel(self, session_id: str, task_id: str) -> bool: ...




