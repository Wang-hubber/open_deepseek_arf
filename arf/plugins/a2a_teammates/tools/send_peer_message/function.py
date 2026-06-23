"""send_peer_message — send a message to another peer agent via session_id."""
from __future__ import annotations

import uuid

from arf.core.protocols.communication import AgentMessage
from arf.plugins.a2a_teammates.tools import _registry


async def execute(
    to: str,
    message: str,
    type: str = "task",
    priority: str = "normal",
    session_id: str = "",
) -> dict:
    """Send a peer message to *to* (target session_id) via the AgentBus.

    *session_id* is injected by the plugin at before_tools — the caller
    does not provide it.  The sender is the calling agent's own session_id.

    Message lands in the receiver's inbox and is injected at the next
    before_model hook.  When the receiver calls task_complete, the reply
    is auto-forwarded back to the sender.
    """
    if _registry.agent_bus is None:
        return {
            "ok": False,
            "error": "AgentBus not initialized — is the a2a_teammates plugin enabled?",
        }

    if not session_id:
        return {"ok": False, "error": "session_id not provided — plugin must inject it"}

    to = to.strip()
    correlation_id = f"peer_{uuid.uuid4().hex[:8]}"

    msg = AgentMessage(
        sender=session_id,
        receiver=to,
        type=type,
        payload={"message": message},
        priority=priority,
        correlation_id=correlation_id,
    )

    await _registry.agent_bus.send(msg)

    _registry._pending_replies[correlation_id] = {
        "sender": session_id,
        "receiver": to,
    }

    return {"ok": True, "correlation_id": correlation_id}
