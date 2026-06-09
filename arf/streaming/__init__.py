"""Streaming transport — SSE and NDJSON adapters for HTTP streaming.

Thin format translators that wrap ``BaseAgent.astream()`` (async generator
of ``AgentEvent``).  No server, framework, or HTTP dependency — the caller
is responsible for serving the output (e.g. FastAPI ``StreamingResponse``
or Starlette ``EventSourceResponse``).

Usage::

    from arf.streaming import SSEStreamAdapter, NDJSONStreamAdapter

    sse = SSEStreamAdapter(agent)
    async for chunk in sse.stream("hello"):
        # write chunk to HTTP response body
        ...

Events are serialised with ``AgentEvent.model_dump(exclude_none=True)``.
"""

from arf.streaming.adapters import SSEStreamAdapter, NDJSONStreamAdapter

__all__ = ["SSEStreamAdapter", "NDJSONStreamAdapter"]
