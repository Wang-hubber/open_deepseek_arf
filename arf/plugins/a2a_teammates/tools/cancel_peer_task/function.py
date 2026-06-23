"""cancel_peer_task — send a JRPC notification to cancel a pending task."""
from __future__ import annotations

from arf.communication.jrpc import JrpcEnvelope
from arf.core.protocols.communication import AgentMessage
from arf.plugins.a2a_teammates.state import get_state


async def execute(correlation_id: str, session_id: str = "") -> dict:
    """Cancel a previously sent peer task via JRPC notification.

    Pops the pending reply expectation, sends a ``task.cancel``
    notification to the receiver via the AgentBus.
    """
    state = get_state()
    entry = state.pending_replies.pop(correlation_id, None)
    if entry is None:
        return {"ok": False, "error": f"no pending task '{correlation_id}'"}

    from arf.plugins.a2a_teammates.state import save_pending_replies
    await save_pending_replies()

    receiver_sid = entry["receiver"]

    if state.agent_bus is not None:
        await state.agent_bus.send(AgentMessage(
            sender=session_id,
            receiver=receiver_sid,
            type="notification",
            payload=JrpcEnvelope.notification(
                method=JrpcEnvelope.METHOD_CANCEL,
                params={"correlation_id": correlation_id},
            ),
            correlation_id=f"cancel_{correlation_id}",
        ))

    return {"ok": True, "cancelled": True}
