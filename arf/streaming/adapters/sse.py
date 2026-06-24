"""SSEStreamAdapter — wraps agent.astream() as SSE (text/event-stream) lines."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from arf.streaming.adapters._serialize import event_to_dict

logger = logging.getLogger("arf.streaming.sse")


class SSEStreamAdapter:
    """Wraps a BaseAgent's astream() and yields SSE-formatted bytes.

    Usage::

        adapter = SSEStreamAdapter(agent)
        async for line in adapter.stream("hello", session_id="sid"):
            # line is b"data: {...}\\n\\n"
            yield line
    """

    def __init__(self, agent) -> None:
        self._agent = agent

    async def stream(
        self, user_message: str, session_id: str = ""
    ) -> AsyncIterator[bytes]:
        """Yield SSE lines from agent.astream()."""
        astream = self._agent.astream(user_message, session_id=session_id)
        async for event in astream:
            try:
                payload = event_to_dict(event)
                line = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                yield line.encode("utf-8")
            except Exception:
                logger.exception("Failed to serialize event %s", event.type)
