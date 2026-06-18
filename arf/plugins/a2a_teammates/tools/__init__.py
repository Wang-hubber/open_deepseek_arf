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
    """Notify that a peer message is waiting on the AgentBus.

    Called by send_peer_message after dropping the message on the bus.
    The receiver agent is expected to pick up the message via its
    pre_action hook when it next runs astream().  The frontend triggers
    this by connecting to /chat/{agent}/stream with the peer session_id.

    We no longer call agent.chat() here — that used invoke() which is
    non-streaming and opaque.  Instead the frontend connects an SSE
    stream and the agent's astream() loop picks up the bus message
    naturally via its a2a_teammates pre_action hook.
    """
    if _registry.agents.get(receiver) is None:
        logger.warning("Peer agent '%s' not registered for wake-up", receiver)
        return
    session_id = f"{group_id}__{receiver}" if group_id else ""
    logger.info("Peer message for '%s' from '%s' waiting on bus (sid=%s) "
                "— frontend SSE connection will trigger processing",
                receiver, sender, session_id or "(new)")
