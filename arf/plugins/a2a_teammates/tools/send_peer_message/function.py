"""send_peer_message — send a JRPC request to another peer agent."""
from __future__ import annotations

import uuid

from arf.communication.jrpc import JrpcEnvelope
from arf.core.protocols.communication import AgentMessage
from arf.plugins.a2a_teammates.state import (
    get_bus,
    get_pending_replies,
    get_registered_sids,
)


_METHOD_MAP = {
    "task": JrpcEnvelope.METHOD_ASSIGN,
}


async def execute(
    to: str,
    message: str,
    type: str = "task",
    priority: str = "normal",
    session_id: str = "",
) -> dict:
    """Send a JRPC request to *to* (target session_id) via their AgentBus.

    *session_id* is injected by the plugin at before_tools — the caller
    does not provide it.  The sender is the calling agent's own session_id.
    """
    to = to.strip()
    target_bus = get_bus(to)
    if target_bus is None:
        return {
            "ok": False,
            "error": f"Target agent '{to}' not found in bus registry. "
                      f"Registered: {list(get_bus.__globals__.get('_bus_registry', {}).keys())}",
        }

    if not session_id:
        return {"ok": False, "error": "session_id not provided — plugin must inject it"}

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

    print(f"[A2A] send_peer_message | to={to} sender={session_id} corr={correlation_id} bus={hex(id(target_bus))} registered={get_registered_sids()}")
    await target_bus.send(msg)

    get_pending_replies()[correlation_id] = {
        "sender": session_id,
        "receiver": to,
        "created_at": __import__("time").time(),
    }

    from arf.plugins.a2a_teammates.state import save_pending_replies
    await save_pending_replies()

    return {"ok": True, "correlation_id": correlation_id}
