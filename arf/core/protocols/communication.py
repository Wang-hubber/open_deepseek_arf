"""Protocols for multi-agent communication."""
from typing import Protocol, AsyncIterator
from dataclasses import dataclass
from typing import Literal


@dataclass
class AgentMessage:
    sender: str
    receiver: str | None
    type: Literal["task_delegate", "info", "query"]
    payload: dict
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


class PeerAgent(Protocol):
    async def broadcast(self, message: AgentMessage) -> None: ...
    async def negotiate(self, proposal: dict, peers: list[str]) -> dict: ...


class TaskDelegator(Protocol):
    async def delegate(self, task: dict, from_agent: str, to_agent: str) -> str: ...
    async def get_result(self, handle_id: str, timeout: int) -> dict: ...


class Supervisor(Protocol):
    async def route_task(self, task: dict, agents: list[AgentInfo]) -> str: ...
    async def should_intervene(self, handle_id: str, progress: dict) -> bool: ...
    async def synthesize(self, results: list[dict]) -> str: ...


class SharedWorkspace(Protocol):
    async def write(self, key: str, value: dict, owner: str) -> None: ...
    async def read(self, key: str) -> dict | None: ...


class Lock(Protocol):
    async def acquire(self, key: str, owner: str, ttl: float = 30.0) -> bool: ...
    async def release(self, key: str, owner: str) -> None: ...


class ConsensusProtocol(Protocol):
    async def propose(self, proposal: dict, voters: list[str]) -> dict: ...
    async def vote(self, proposal_id: str, vote: str) -> None: ...
