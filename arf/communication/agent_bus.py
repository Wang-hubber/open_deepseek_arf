"""InMemoryAgentBus — AgentBus protocol implementation for peer message routing."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import AsyncIterator

from arf.core.protocols.communication import AgentBus, AgentMessage, AgentInfo


class InMemoryAgentBus:
    """In-memory message bus implementing the AgentBus protocol.

    Messages are stored in per-receiver inbox deques. send() writes to
    the inbox; receive() drains it as an async iterator. register() and
    discover() maintain an online agent catalog.
    """

    def __init__(self) -> None:
        self._inboxes: dict[str, deque[AgentMessage]] = defaultdict(deque)
        self._agents: dict[str, AgentInfo] = {}

    async def send(self, message: AgentMessage) -> None:
        """Deliver a message to the receiver's inbox.

        If receiver is None, broadcasts to all registered agents
        except the sender.
        """
        if message.receiver is None:
            # Broadcast to all except sender
            for name in self._agents:
                if name != message.sender:
                    self._inboxes[name].append(message)
        else:
            self._inboxes[message.receiver].append(message)

    async def receive(self, agent_name: str) -> AsyncIterator[AgentMessage]:
        """Drain and yield all messages from *agent_name*'s inbox.

        Consuming read — calling receive() again returns only new messages
        delivered after the first call.
        """
        inbox = self._inboxes[agent_name]
        while inbox:
            yield inbox.popleft()
        # Yield control so caller sees an empty iterator if no messages
        # (avoids blocking — caller's async for loops cleanly with 0 iterations)

    async def register(self, agent: AgentInfo) -> None:
        """Register an agent with the bus."""
        self._agents[agent.name] = agent

    async def discover(self, capability: str | None = None) -> list[AgentInfo]:
        """Return registered agents, optionally filtered by capability."""
        agents = list(self._agents.values())
        if capability is None:
            return agents
        return [a for a in agents if capability in a.capabilities]
