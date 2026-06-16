"""Tests for SSE and NDJSON streaming adapters."""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from arf.core.events import AgentEvent
from arf.streaming import SSEStreamAdapter, NDJSONStreamAdapter


class TestSSEStreamAdapter:
    def test_yields_sse_formatted_chunks(self):
        """Each event is emitted as ``data: <json>\\n\\n``."""
        agent = MagicMock()

        async def fake_astream(_msg, _sid="default", **_kw):
            yield AgentEvent(type="session_start", session_id="s1", turn=0,
                             data={"ok": True})
            yield AgentEvent(type="text", session_id="s1", turn=1,
                             data={"content": "hello"})

        agent.astream = fake_astream
        adapter = SSEStreamAdapter(agent)

        async def collect():
            chunks = []
            async for chunk in adapter.stream("hi"):
                chunks.append(chunk.decode("utf-8"))
            return chunks

        chunks = asyncio.run(collect())
        assert len(chunks) == 2
        assert all(c.startswith("data: ") for c in chunks)
        assert all(c.endswith("\n\n") for c in chunks)

        # Verify the JSON payloads are valid
        payloads = [json.loads(c[len("data: "):].rstrip("\n")) for c in chunks]
        assert payloads[0]["type"] == "session_start"
        assert payloads[1]["type"] == "text"
        assert payloads[1]["data"]["content"] == "hello"

    def test_empty_stream_produces_no_output(self):
        """No events means no SSE chunks."""
        agent = MagicMock()

        async def empty_astream(_msg, _sid="default", **_kw):
            if False:
                yield  # pragma: no cover

        agent.astream = empty_astream
        adapter = SSEStreamAdapter(agent)

        chunks = asyncio.run(_collect_all(adapter.stream("hi")))
        assert chunks == []


class TestNDJSONStreamAdapter:
    def test_yields_ndjson_formatted_chunks(self):
        """Each event is ``<json>\\n`` — one line per event."""
        agent = MagicMock()

        async def fake_astream(_msg, _sid="default", **_kw):
            yield AgentEvent(type="session_start", session_id="s2", turn=0,
                             data={"ok": True})
            yield AgentEvent(type="tool_call_start", session_id="s2", turn=1,
                             data={"tool_name": "read"})

        agent.astream = fake_astream
        adapter = NDJSONStreamAdapter(agent)

        chunks = asyncio.run(_collect_all(adapter.stream("hi")))
        assert len(chunks) == 2

        lines = [c.decode("utf-8") for c in chunks]
        for line in lines:
            obj = json.loads(line.rstrip("\n"))
            assert "type" in obj
            assert "session_id" in obj


def _collect_all(agen):
    async def _run():
        result = []
        async for chunk in agen:
            result.append(chunk)
        return result
    return _run()
