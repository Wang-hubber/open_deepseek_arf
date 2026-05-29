"""InMemoryAgentBus — asyncio.Queue-backed agent message routing."""
import asyncio
from arf.core.protocols.communication import AgentMessage, AgentInfo


class InMemoryAgentBus:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}
        self._agents: dict[str, AgentInfo] = {}
        self.sent_messages: list[AgentMessage] = []

    async def send(self, message: AgentMessage, timeout: float | None = None) -> None:
        self.sent_messages.append(message)
        targets = [message.receiver] if message.receiver else list(self._queues.keys())
        for name in targets:
            # Auto-create queue for receivers that haven't registered yet —
            # avoids race where receive()'s async generator hasn't started.
            if name not in self._queues:
                self._queues[name] = asyncio.Queue(maxsize=100)
            if timeout is not None:
                await asyncio.wait_for(
                    self._queues[name].put(message), timeout=timeout
                )
            else:
                await self._queues[name].put(message)

    async def receive(self, agent_name: str):
        if agent_name not in self._queues:
            self._queues[agent_name] = asyncio.Queue(maxsize=100)
        q = self._queues[agent_name]
        while True:
            yield await q.get()

    async def register(self, agent: AgentInfo) -> None:
        self._agents[agent.name] = agent
        self._queues.setdefault(agent.name, asyncio.Queue(maxsize=100))

    async def discover(self, capability: str | None = None) -> list[AgentInfo]:
        if capability:
            return [a for a in self._agents.values() if capability in a.capabilities]
        return list(self._agents.values())

    def reset(self) -> None:
        self.sent_messages.clear()
        self._queues.clear()
        self._agents.clear()
