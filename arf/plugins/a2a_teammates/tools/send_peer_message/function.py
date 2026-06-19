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
    pre_action hook together with a team communication context.
    The reply is captured by round_end / task_completed hooks and
    forwarded back to the sender automatically.
    """
    if _registry.agent_bus is None:
        return {"ok": False, "error": "AgentBus not initialized — is the a2a_teammates plugin enabled?"}

    # Normalize receiver to lowercase
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

    import logging
    logger = logging.getLogger("arf.plugins.a2a_teammates.tools")
    logger.info("Peer message from '%s' to '%s' sent via bus (corr=%s)",
                sender, receiver, correlation_id)

    return {"ok": True, "correlation_id": correlation_id}
