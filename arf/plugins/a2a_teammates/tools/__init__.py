"""A2A Teammates plugin tools."""
import asyncio
import logging

logger = logging.getLogger("arf.plugins.a2a_teammates.tools")


class _TeammatesRegistry:
    """Module-level singleton bridging plugin and tool functions."""

    def __init__(self) -> None:
        self.agent_bus: object | None = None
        self.agents: dict[str, object] = {}  # role_name → agent instance


_registry = _TeammatesRegistry()


async def _wake_receiver(receiver: str, message: str, sender: str,
                         group_id: str = ""):
    """Wake up a peer agent to process an incoming AgentBus message.

    Called by send_peer_message after dropping the message on the bus.
    Runs agent.chat() to completion, then forwards the response back
    to the sender via AgentBus so the sender can pick it up in its
    next pre_action hook (or via pre-injection on reconnect).
    """
    agent = _registry.agents.get(receiver)
    if agent is None:
        logger.warning("Peer agent '%s' not registered for wake-up", receiver)
        return

    try:
        session_id = f"{group_id}__{receiver}" if group_id else ""
        logger.info("Waking agent '%s' for peer message from '%s' (sid=%s)",
                    receiver, sender, session_id or "(new)")
        result = await agent.chat(message, session_id=session_id)

        # Forward chat() result as fallback.  The peer agent should send
        # its own reply via send_peer_message during processing (preferred).
        # This fallback catches cases where the agent didn't explicitly reply.
        if result and _registry.agent_bus:
            from arf.core.protocols.communication import AgentMessage
            import uuid
            reply = AgentMessage(
                sender=receiver,
                receiver=sender,
                type="answer",
                payload={"message": str(result)[:2000]},
                priority="normal",
                correlation_id=f"peer_rpl_{uuid.uuid4().hex[:8]}",
            )
            await _registry.agent_bus.send(reply)
            logger.warning("Peer '%s' fallback reply forwarded to '%s' (%d chars)",
                        receiver, sender, len(str(result)))
        elif not result:
            logger.warning("Peer '%s' chat returned empty — no fallback reply", receiver)
    except Exception:
        logger.exception("Peer agent '%s' wake-up failed", receiver)
