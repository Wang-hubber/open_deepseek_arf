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
    Creates a background task so the sender is not blocked.
    """
    agent = _registry.agents.get(receiver)
    if agent is None:
        logger.warning("Peer agent '%s' not registered for wake-up", receiver)
        return

    try:
        # Use a session_id derived from group_id so sessions are stable
        session_id = f"{group_id}__{receiver}" if group_id else ""
        logger.info("Waking agent '%s' for peer message from '%s' (sid=%s)",
                    receiver, sender, session_id or "(new)")
        await agent.chat(message, session_id=session_id)
    except Exception:
        logger.exception("Peer agent '%s' wake-up failed", receiver)
