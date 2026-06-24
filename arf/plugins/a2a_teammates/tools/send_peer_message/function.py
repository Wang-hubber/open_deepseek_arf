"""send_peer_message — send a JRPC request to another peer agent."""
from __future__ import annotations

import uuid

from arf.communication.jrpc import JrpcEnvelope
from arf.core.protocols.communication import AgentMessage
from arf.plugins.a2a_teammates.state import (
    get_bus,
    get_pending_replies,
    get_registered_sids,
    _dbg,
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
    _register_wait=None,
) -> dict:
    """Send a JRPC request to *to* (target session_id) via their AgentBus.

    *session_id* is injected by the plugin at before_tools — the caller
    does not provide it.  The sender is the calling agent's own session_id.
    *_register_wait* is injected by the engine — registers a before_round
    wait so the harness parks before the next round while awaiting a reply.
    """
    _dbg(f"send_peer_message ENTER | raw_to={to!r} session_id={session_id!r}")
    to = to.strip()
    target_bus = get_bus(to)
    if target_bus is None:
        return {
            "ok": False,
            "error": f"Target agent '{to}' not found in bus registry. "
                      f"Registered: {get_registered_sids()}",
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

    _dbg(f"send_peer_message SEND | to={to} sender={session_id} corr={correlation_id} bus={hex(id(target_bus))} registered={get_registered_sids()}")
    await target_bus.send(msg)

    # Register wait on before_round — harness parks next round
    wi = None
    if _register_wait is not None:
        wi = _register_wait(
            "before_round",
            f"peer_wait:{correlation_id}",
            resume_key=f"peer_wait:{correlation_id}",
        )

    get_pending_replies()[correlation_id] = {
        "sender": session_id,
        "receiver": to,
        "created_at": __import__("time").time(),
    }

    from arf.plugins.a2a_teammates.state import save_pending_replies
    await save_pending_replies()

    return {
        "ok": True,
        "correlation_id": correlation_id,
        "wait_id": wi.wait_id if wi else "",
    }
