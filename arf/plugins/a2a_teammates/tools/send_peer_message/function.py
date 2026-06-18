"""send_peer_message — send a message to another peer agent."""
from __future__ import annotations

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
    """
    if _registry.agent_bus is None:
        return {"ok": False, "error": "AgentBus not initialized — is the a2a_teammates plugin enabled?"}

    # Infer sender role from session_id: {group_id}__{role}
    from arf.session.session_index import SessionIndex
    parsed = SessionIndex.parse_session_id(session_id) if session_id else None
    sender = parsed[1] if parsed else "unknown"

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
    return {"ok": True, "correlation_id": correlation_id}
