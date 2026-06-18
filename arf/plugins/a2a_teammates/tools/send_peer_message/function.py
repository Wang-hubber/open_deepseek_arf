"""send_peer_message — send a message to another peer agent."""
from __future__ import annotations

import asyncio
import uuid

from arf.core.protocols.communication import AgentMessage
from arf.plugins.a2a_teammates.tools import _registry


async def execute(
    receiver: str,
    message: str,
    type: str = "info",
    priority: str = "normal",
    _engine=None,
    session_id: str = "",
) -> dict:
    """Send a peer message to *receiver* via the AgentBus.

    The sender is inferred from *session_id* (the caller's session).
    Message lands in the receiver's inbox and is injected at the next
    pre_action hook (normal) or immediately (urgent).

    If the receiver is a registered peer agent, also wakes it up to
    process the message (background task, non-blocking for sender).
    """
    if _registry.agent_bus is None:
        return {"ok": False, "error": "AgentBus not initialized — is the a2a_teammates plugin enabled?"}

    # Normalize receiver to lowercase — models often capitalize role names
    receiver = receiver.lower().strip()

    # Infer sender role from session_id: {group_id}__{role}
    from arf.session.session_index import SessionIndex
    parsed = SessionIndex.parse_session_id(session_id) if session_id else None
    sender = parsed[1] if parsed else "unknown"
    group_id = parsed[0] if parsed else ""

    correlation_id = f"peer_{uuid.uuid4().hex[:8]}"

    msg = AgentMessage(
        sender=sender,
        receiver=receiver,
        type=type,
        payload={"message": message},
        priority=priority,
        correlation_id=correlation_id,
    )

    await _registry.agent_bus.send(msg)

    # Wake up the receiver agent to process the message (background task)
    if receiver in _registry.agents:
        from arf.plugins.a2a_teammates.tools import _wake_receiver
        loop = asyncio.get_event_loop()
        loop.create_task(
            _wake_receiver(receiver, message, sender, group_id)
        )

    return {"ok": True, "correlation_id": correlation_id}
