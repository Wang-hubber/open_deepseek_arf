"""InMemoryAgentBus — AgentBus protocol implementation for peer message routing."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import AsyncIterator

from arf.core.protocols.communication import AgentBus, AgentMessage, AgentInfo


class InMemoryAgentBus:
    """In-memory message bus implementing the AgentBus protocol.

    Messages are stored in per-receiver inbox deques. send() writes to
    the inbox; receive() drains it as an async iterator. register() and
    discover() maintain an online agent catalog.

    wait_for_message() provides a push notification mechanism — callers
    can block until a message arrives for a specific agent, with optional
    timeout and cancellation support.
    """

    def __init__(self) -> None:
        self._inboxes: dict[str, deque[AgentMessage]] = defaultdict(deque)
        self._agents: dict[str, AgentInfo] = {}
        self._events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)

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
                    self._events[name].set()
        else:
            self._inboxes[message.receiver].append(message)
            self._events[message.receiver].set()

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

    async def wait_for_message(
        self,
        agent_name: str,
        timeout: float | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> bool:
        """Block until a message arrives for *agent_name*.

        Returns True if a message is available (caller should then drain
        via receive()). Returns False on timeout or cancellation.

        Args:
            agent_name: The agent to wait for messages for.
            timeout: Maximum seconds to wait, or None for indefinite.
            cancel_event: If set, the wait is cancelled and returns False.
        """
        # Fast path: messages already queued
        if self._inboxes.get(agent_name):
            return True

        event = self._events[agent_name]
        event.clear()
        # Re-check after clear: sender may have delivered between
        # the first check and event.clear(), leaving a message in
        # the inbox with a cleared event.
        if self._inboxes.get(agent_name):
            return True

        # Build list of awaitables to wait on
        wait_tasks = [asyncio.create_task(event.wait())]
        cancel_task = None

        if cancel_event is not None:
            cancel_task = asyncio.create_task(cancel_event.wait())
            wait_tasks.append(cancel_task)

        try:
            done, _ = await asyncio.wait(
                wait_tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Check if we were cancelled
            if cancel_task is not None and cancel_task in done:
                return False
            # Check if event was set (message arrived)
            if event.is_set():
                return True
            # Timeout
            return False
        finally:
            # Clean up pending tasks
            for t in wait_tasks:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

    async def register(self, agent: AgentInfo) -> None:
        """Register an agent with the bus."""
        self._agents[agent.name] = agent

    async def deregister(self, agent_name: str) -> None:
        """Remove an agent from the bus and clean up its resources."""
        self._agents.pop(agent_name, None)
        self._inboxes.pop(agent_name, None)
        self._events.pop(agent_name, None)

    async def discover(self, capability: str | None = None) -> list[AgentInfo]:
        """Return registered agents, optionally filtered by capability."""
        agents = list(self._agents.values())
        if capability is None:
            return agents
        return [a for a in agents if capability in a.capabilities]
