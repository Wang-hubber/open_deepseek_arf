"""send_peer_message — send a JRPC request to another peer agent."""
from __future__ import annotations

import uuid

from arf.communication.jrpc import JrpcEnvelope
from arf.core.protocols.communication import AgentMessage
from arf.plugins.a2a_teammates.state import get_state


_METHOD_MAP = {
    "task": JrpcEnvelope.METHOD_ASSIGN,
    "info": JrpcEnvelope.METHOD_INFO,
}


async def execute(
    to: str,
    message: str,
    type: str = "task",
    priority: str = "normal",
    session_id: str = "",
) -> dict:
    """Send a JRPC request to *to* (target session_id) via the AgentBus.

    *session_id* is injected by the plugin at before_tools — the caller
    does not provide it.  The sender is the calling agent's own session_id.

    The LLM's ``message`` string becomes ``params.message`` inside a
    JRPC request envelope.  The receiver's plugin unwraps the envelope
    and injects only the plain text content to the model.
    """
    state = get_state()
    if state.agent_bus is None:
        return {
            "ok": False,
            "error": "AgentBus not initialized — is the a2a_teammates plugin enabled?",
        }

    if not session_id:
        return {"ok": False, "error": "session_id not provided — plugin must inject it"}

    to = to.strip()
    correlation_id = f"peer_{uuid.uuid4().hex[:8]}"
    method = _METHOD_MAP.get(type, JrpcEnvelope.METHOD_ASSIGN)

    msg = AgentMessage(
        sender=session_id,
        receiver=to,
        type="request",
        payload=JrpcEnvelope.request(
            method=method,
            params={"message": message},
            id=correlation_id,
        ),
        priority=priority,
        correlation_id=correlation_id,
    )

    await state.agent_bus.send(msg)

    state.pending_replies[correlation_id] = {
        "sender": session_id,
        "receiver": to,
        "created_at": __import__("time").time(),
    }

    from arf.plugins.a2a_teammates.state import save_pending_replies
    await save_pending_replies()

    return {"ok": True, "correlation_id": correlation_id}
