"""send_peer_message — send a message to another peer agent."""
from __future__ import annotations

import uuid

from arf.core.protocols.communication import AgentMessage
from arf.plugins.a2a_teammates.tools import _registry


async def execute(
    receiver: str,
    message: str,
    type: str = "task",
    priority: str = "normal",
    session_id: str = "",
) -> dict:
    """Send a peer message to *receiver* via the AgentBus.

    The sender is inferred from *session_id* (the caller's session).
    Message lands in the receiver's inbox and is injected at the next
    before_model hook. When the receiver calls task_complete, the
    reply is auto-forwarded back to the sender.

    Use type="task" to assign work (receiver should call task_complete
    when done). Use type="info" for notifications without reply.
    """
    if _registry.agent_bus is None:
        return {
            "ok": False,
            "error": "AgentBus not initialized — is the a2a_teammates plugin enabled?",
        }

    receiver = receiver.lower().strip()

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

    # Register reply expectation — forward_reply uses this to route
    # the task_complete result back to the sender
    _registry._pending_replies[correlation_id] = {
        "sender": sender,
        "receiver": receiver,
    }

    return {"ok": True, "correlation_id": correlation_id}
