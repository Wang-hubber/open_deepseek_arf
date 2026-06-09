"""SSE (Server-Sent Events) adapter for ``BaseAgent.astream()``.

Converts ``AgentEvent`` objects to ``text/event-stream`` lines suitable
for ``StreamingResponse`` (FastAPI/Starlette) or raw ASGI responses.

The adapter is stateless — it simply translates an async generator of
events into an async generator of byte-strings.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from arf.agent.base import BaseAgent
from arf.streaming.adapters._serialize import event_to_dict


class SSEStreamAdapter:
    """Thin SSE wrapper around ``agent.astream()``.

    Usage with FastAPI::

        @app.get("/stream")
        async def stream():
            async def events():
                async for chunk in SSEStreamAdapter(agent).stream("hello"):
                    yield chunk
            return StreamingResponse(events(), media_type="text/event-stream")
    """

    def __init__(self, agent: BaseAgent) -> None:
        self._agent = agent

    async def stream(
        self, user_message: str, session_id: str = "default",
    ) -> AsyncIterator[bytes]:
        """Yield ``data: <json>\\n\\n`` chunks from *agent.astream()*."""
        async for event in self._agent.astream(user_message, session_id):
            payload = event_to_dict(event)
            line = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield line.encode("utf-8")
