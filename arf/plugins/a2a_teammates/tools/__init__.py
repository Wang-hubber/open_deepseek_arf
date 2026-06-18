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
        chat_result = await agent.chat(message, session_id=session_id)

        # Collect all substantive output from the conversation, not just the
        # last assistant message (which may be a progress note like "checking
        # status").  Scan the saved state for all assistant messages and tool
        # results to build a comprehensive reply.
        reply_text = str(chat_result or "")
        try:
            import json as _json
            from pathlib import Path as _Path
            state_file = _Path(agent._engine._data_dir) / session_id / "state" / f"{session_id}.json"
            if state_file.exists():
                state = _json.loads(state_file.read_text(encoding="utf-8"))
                parts = []
                for m in state.get("messages", []):
                    if m.get("role") == "assistant" and m.get("content"):
                        parts.append(m["content"])
                    elif m.get("role") == "tool":
                        c = str(m.get("content", ""))
                        # Include sub-agent results (A2A task outputs)
                        if "[A2A]" in c or "Result:" in c or "result" in c[:50].lower():
                            parts.append(c[:800])
                if parts:
                    # Deduplicate: keep the last occurrence of each unique content prefix
                    seen = set()
                    deduped = []
                    for p in reversed(parts):
                        key = p[:60]
                        if key not in seen:
                            seen.add(key)
                            deduped.append(p)
                    deduped.reverse()
                    reply_text = "\n\n---\n".join(deduped)
        except Exception:
            pass  # Fall back to chat() return value

        # Forward reply back to sender via AgentBus
        if reply_text and _registry.agent_bus:
            from arf.core.protocols.communication import AgentMessage
            import uuid
            reply = AgentMessage(
                sender=receiver,
                receiver=sender,
                type="answer",
                payload={"message": reply_text[:3000]},
                priority="normal",
                correlation_id=f"peer_rpl_{uuid.uuid4().hex[:8]}",
            )
            await _registry.agent_bus.send(reply)
            logger.warning("Peer '%s' reply forwarded to '%s' (%d chars) [corr=%s]",
                        receiver, sender, len(reply_text), reply.correlation_id)
        else:
            logger.warning("Peer '%s' chat completed but reply NOT forwarded "
                        "(result=%s, bus=%s)", receiver,
                        'present' if reply_text else 'empty',
                        'present' if _registry.agent_bus else 'none')
    except Exception:
        logger.exception("Peer agent '%s' wake-up failed", receiver)
