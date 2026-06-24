"""InMemoryAgentBus — AgentBus protocol implementation for peer message routing."""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from collections import defaultdict, deque
from pathlib import Path as _Path
from typing import AsyncIterator

from arf.core.protocols.communication import AgentBus, AgentMessage, AgentInfo

logger = logging.getLogger("arf.communication.agent_bus")

_BUS_LOG_PATH: str | None = None


def set_bus_log_dir(data_dir: str) -> None:
    global _BUS_LOG_PATH
    _BUS_LOG_PATH = str(_Path(data_dir) / "debug_bus.log")
    _Path(_BUS_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)


def _buslog(msg: str) -> None:
    ts = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [BUS] {msg}"
    print(line, flush=True)
    if _BUS_LOG_PATH:
        try:
            with open(_BUS_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


class InMemoryAgentBus:
    """In-memory message bus implementing the AgentBus protocol.

    Messages are stored in per-receiver inbox deques. send() writes to
    the inbox; receive() drains it as an async iterator. register() and
    discover() maintain an online agent catalog.

    wait_for_message() provides a push notification mechanism — callers
    can block until a message arrives for a specific agent, with optional
    timeout and cancellation support.

    Args:
        max_inbox_size: Per-receiver max queue depth. When exceeded, the
            oldest message is dropped with a warning. 0 means unlimited.
    """

    def __init__(self, max_inbox_size: int = 0) -> None:
        self._inboxes: dict[str, deque[AgentMessage]] = defaultdict(deque)
        self._agents: dict[str, AgentInfo] = {}
        self._events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self._max_inbox_size = max_inbox_size

    async def send(self, message: AgentMessage) -> None:
        """Deliver a message to the receiver's inbox.

        If receiver is None, broadcasts to all registered agents
        except the sender.
        """
        receivers: list[str] = []
        if message.receiver is None:
            receivers = [n for n in self._agents if n != message.sender]
        else:
            receivers = [message.receiver]

        for name in receivers:
            inbox = self._inboxes[name]
            if self._max_inbox_size > 0 and len(inbox) >= self._max_inbox_size:
                dropped = inbox.popleft()
                logger.warning(
                    "Inbox for %s full (%d), dropped oldest msg from %s",
                    name, self._max_inbox_size, dropped.sender,
                )
            inbox.append(message)
            ev = self._events[name]
            ev.set()
            _buslog(f"send | bus={hex(id(self))} key={name} event={hex(id(ev))} inbox={len(inbox)} corr={getattr(message, 'correlation_id', '-')}")

    async def receive(self, agent_name: str) -> AsyncIterator[AgentMessage]:
        """Drain and yield all messages from *agent_name*'s inbox.

        Consuming read — calling receive() again returns only new messages
        delivered after the first call.
        """
        inbox = self._inboxes[agent_name]
        while inbox:
            yield inbox.popleft()

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
        inbox = self._inboxes.get(agent_name)
        if inbox:
            _buslog(f"wait fast-hit | bus={hex(id(self))} key={agent_name} inbox={len(inbox)}")
            return True

        event = self._events[agent_name]
        event.clear()
        # Re-check after clear: sender may have delivered between
        # the first check and event.clear(), leaving a message in
        # the inbox with a cleared event.
        inbox = self._inboxes.get(agent_name)
        if inbox:
            _buslog(f"wait recheck-hit | bus={hex(id(self))} key={agent_name} inbox={len(inbox)} event={hex(id(event))}")
            return True

        _buslog(f"wait blocking | bus={hex(id(self))} key={agent_name} event={hex(id(event))} timeout={timeout}")

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
                _buslog(f"wait cancelled | bus={hex(id(self))} key={agent_name}")
                return False
            # Check if event was set (message arrived)
            if event.is_set():
                _buslog(f"wait woke-event | bus={hex(id(self))} key={agent_name} event={hex(id(event))} inbox={len(self._inboxes.get(agent_name, []))}")
                return True
            # Timeout
            _buslog(f"wait timeout | bus={hex(id(self))} key={agent_name} event={hex(id(event))}")
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
