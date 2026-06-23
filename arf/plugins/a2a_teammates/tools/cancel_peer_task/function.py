"""cancel_peer_task — cancel a pending peer task by correlation_id."""
from __future__ import annotations

from arf.core.protocols.communication import AgentMessage
from arf.plugins.a2a_teammates.tools import _registry


async def execute(correlation_id: str, session_id: str = "") -> dict:
    """Cancel a previously sent peer task.

    Pops the pending reply expectation, sends a cancel message to the
    receiver via the AgentBus, and wakes the receiver if they are
    currently parked waiting for messages.
    """
    entry = _registry._pending_replies.pop(correlation_id, None)
    if entry is None:
        return {"ok": False, "error": f"no pending task '{correlation_id}'"}

    receiver_sid = entry["receiver"]

    if _registry.agent_bus is not None:
        await _registry.agent_bus.send(AgentMessage(
            sender=session_id,
            receiver=receiver_sid,
            type="cancel",
            payload={"correlation_id": correlation_id},
            correlation_id=f"cancel_{correlation_id}",
        ))

    harness = _registry._peer_harnesses.get(receiver_sid)
    wait_id = _registry._peer_wait_ids.pop(receiver_sid, None)
    if harness is not None and wait_id is not None:
        await harness.resolve_wait(wait_id, inject_message={
            "role": "system",
            "content": (
                f"[Peer cancel from {session_id}] "
                f"Task {correlation_id} cancelled by sender."
            ),
        })

    return {"ok": True, "cancelled": True}
