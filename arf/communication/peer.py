"""PeerAgent — decentralized agent-to-agent communication via AgentBus.

Enables direct P2P messaging without a central Supervisor.  Each peer
registers on the shared bus and can broadcast, query, negotiate, and
hand off tasks to any other registered agent.

This is the framework-level primitive that replaces application-layer
handover configs — agents discover each other dynamically.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator

from arf.core.protocols.communication import (
    AgentBus, AgentInfo, AgentMessage,
)


class PeerAgent:
    """Decentralized P2P agent endpoint built on AgentBus.

    Each agent in a multi-agent system creates one PeerAgent to:
      - register its capabilities on the shared bus
      - broadcast messages to all peers
      - send targeted messages (task delegation, handoff)
      - negotiate proposals with a subset of peers
      - listen for incoming messages

    Does NOT require a Supervisor.  If Supervisor-based orchestration is
    desired, layer it *on top* of PeerAgent — the Supervisor itself is
    just another registered peer.
    """

    def __init__(self, bus: AgentBus, info: AgentInfo) -> None:
        self._bus = bus
        self._info = info
        self._registered = False

    # ---- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Register this peer on the bus.  Must be called before send/receive."""
        if not self._registered:
            await self._bus.register(self._info)
            self._registered = True

    @property
    def name(self) -> str:
        return self._info.name

    @property
    def capabilities(self) -> list[str]:
        return list(self._info.capabilities)

    # ---- messaging ----------------------------------------------------------

    async def broadcast(self, msg_type: str, payload: dict) -> None:
        """Send a message to ALL registered peers (excluding self)."""
        await self._bus.send(AgentMessage(
            sender=self.name,
            receiver=None,           # None = broadcast
            type=msg_type,           # type: ignore
            payload=payload,
            correlation_id=str(uuid.uuid4()),
        ))

    async def send_to(self, target: str, msg_type: str, payload: dict) -> None:
        """Send a targeted message to a specific peer."""
        await self._bus.send(AgentMessage(
            sender=self.name,
            receiver=target,
            type=msg_type,           # type: ignore
            payload=payload,
            correlation_id=str(uuid.uuid4()),
        ))

    async def listen(self) -> AsyncIterator[AgentMessage]:
        """Yield messages addressed to this peer (including broadcasts)."""
        async for msg in self._bus.receive(self.name):
            yield msg

    # ---- discovery ----------------------------------------------------------

    async def discover_peers(self, capability: str | None = None) -> list[AgentInfo]:
        """List registered peers, optionally filtered by capability."""
        peers = await self._bus.discover(capability)
        return [p for p in peers if p.name != self.name]

    async def find_peer(self, capability: str) -> AgentInfo | None:
        """Find the first peer with the given capability."""
        peers = await self.discover_peers(capability)
        return peers[0] if peers else None

    # ---- negotiation --------------------------------------------------------

    async def negotiate(
        self,
        proposal: dict,
        peers: list[str],
        timeout: float = 30.0,
    ) -> dict[str, dict]:
        """Send a proposal to a list of peers and collect their responses.

        Returns a dict of {peer_name: response_payload} for peers that
        responded before the timeout.
        """
        corr_id = str(uuid.uuid4())
        for target in peers:
            await self._bus.send(AgentMessage(
                sender=self.name,
                receiver=target,
                type="query",
                payload={"proposal": proposal},
                correlation_id=corr_id,
            ))

        responses: dict[str, dict] = {}
        deadline = asyncio.get_event_loop().time() + timeout

        async for msg in self._bus.receive(self.name):
            if msg.correlation_id != corr_id:
                continue
            responses[msg.sender] = msg.payload
            if len(responses) >= len(peers):
                break
            if asyncio.get_event_loop().time() >= deadline:
                break

        return responses

    # ---- handoff (convenience) ----------------------------------------------

    async def handoff(
        self,
        task: str,
        context: str = "",
        target_capability: str = "resource_creation",
        timeout: float = 60.0,
    ) -> dict | None:
        """Hand off a task to a peer with the given capability.

        Returns the first response payload, or None if no capable peer
        responded within the timeout.
        """
        peer = await self.find_peer(target_capability)
        if not peer:
            return None

        await self.send_to(peer.name, "handoff", {
            "task": task,
            "context": context,
        })

        async for msg in self._bus.receive(self.name):
            if msg.sender == peer.name and msg.type == "handoff":
                return msg.payload

        return None
