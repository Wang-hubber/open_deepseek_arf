"""NDJSON (Newline-Delimited JSON) adapter for ``BaseAgent.astream()``.

One JSON object per line, suitable for line-by-line consumption in
CLI clients, log pipelines, or browser ``fetch()`` with
``ReadableStream``.

The adapter is stateless — it translates an async generator of events
into an async generator of byte-strings.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from arf.agent.base import BaseAgent
from arf.streaming.adapters._serialize import event_to_dict


class NDJSONStreamAdapter:
    """Thin NDJSON wrapper around ``agent.astream()``.

    Usage::

        async for chunk in NDJSONStreamAdapter(agent).stream("hello"):
            write(chunk)   # one JSON line per event
    """

    def __init__(self, agent: BaseAgent) -> None:
        self._agent = agent

    async def stream(
        self, user_message: str, session_id: str = "default",
    ) -> AsyncIterator[bytes]:
        """Yield ``<json>\\n`` chunks from *agent.astream()*."""
        async for event in self._agent.astream(user_message, session_id):
            payload = event_to_dict(event)
            line = json.dumps(payload, ensure_ascii=False) + "\n"
            yield line.encode("utf-8")
