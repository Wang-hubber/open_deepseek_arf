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
    """Put peer message on AgentBus and schedule reply capture.

    The receiver agent picks up the message via its pre_action hook when
    the frontend connects an SSE stream.  After the session ends, capture
    the last assistant reply and forward it back to the sender.
    """
    if _registry.agents.get(receiver) is None:
        logger.warning("Peer agent '%s' not registered for wake-up", receiver)
        return

    session_id = f"{group_id}__{receiver}" if group_id else ""
    logger.info("Peer message for '%s' from '%s' on bus (sid=%s)",
                receiver, sender, session_id or "(new)")

    # Schedule background capture: when the peer agent's session ends,
    # read its last assistant message and forward to sender via AgentBus.
    async def _capture_reply():
        agent = _registry.agents.get(receiver)
        if agent is None:
            return
        try:
            # Wait for session to complete (frontend SSE drives astream())
            import asyncio as _asyncio
            import json as _json
            from pathlib import Path as _Path
            state_file = _Path(agent._engine._data_dir) / session_id / "state" / f"{session_id}.json"
            # Poll state file until session completes (max 5 min)
            deadline = _asyncio.get_event_loop().time() + 300
            last_size = -1
            while _asyncio.get_event_loop().time() < deadline:
                await _asyncio.sleep(2)
                if not state_file.exists():
                    continue
                size = state_file.stat().st_size
                if size == last_size:
                    # File stopped growing — check if session ended
                    try:
                        state = _json.loads(state_file.read_text(encoding="utf-8"))
                        if state.get("_session_ended"):
                            break
                    except Exception:
                        pass
                last_size = size

            # Read last assistant message
            if state_file.exists():
                state = _json.loads(state_file.read_text(encoding="utf-8"))
                msgs = state.get("messages", [])
                for m in reversed(msgs):
                    if m.get("role") == "assistant" and m.get("content"):
                        reply_text = str(m["content"])[:2000]
                        if _registry.agent_bus:
                            from arf.core.protocols.communication import AgentMessage
                            import uuid
                            reply = AgentMessage(
                                sender=receiver, receiver=sender, type="answer",
                                payload={"message": reply_text},
                                priority="normal",
                                correlation_id=f"peer_rpl_{uuid.uuid4().hex[:8]}",
                            )
                            await _registry.agent_bus.send(reply)
                            logger.warning("Peer '%s' reply (%d chars) forwarded to '%s'",
                                        receiver, len(reply_text), sender)
                        break
        except Exception:
            logger.exception("Peer '%s' reply capture failed", receiver)

    _asyncio = __import__("asyncio")
    _asyncio.get_event_loop().create_task(_capture_reply())
