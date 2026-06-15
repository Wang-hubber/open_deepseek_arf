"""FakeModelAdapter — controllable fake implementing the ModelAdapter interface.

Uses real model_call_end event data from app traces as response templates.
"""
from collections.abc import Generator


class FakeResponse:
    """Mimics the return value of ModelAdapter.chat_complete()."""
    __slots__ = ("content", "tool_calls", "usage", "reasoning")

    def __init__(self, content="", tool_calls=None, usage=None, reasoning=""):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        self.reasoning = reasoning


class FakeModelAdapter:
    """Controllable fake with the same interface as ModelAdapter.

    Usage:
        # Sequence mode: consume one response per chat_complete() call
        fake = FakeModelAdapter(responses=[
            FakeResponse(content="first response"),
            FakeResponse(content="second response",
                         tool_calls=[{"id": "call_0", "name": "file_reader", "params": {"path": "test"}}]),
        ], default=FakeResponse(content="default fallback"))

        # Error injection
        fake = FakeModelAdapter(raise_on_call=RuntimeError("API down"))

        # Stream mode
        fake = FakeModelAdapter(stream_chunks=[
            {"type": "chunk", "content": "Hello"},
            {"type": "chunk", "content": " world"},
        ])

        # Factory from trace files
        fake = FakeModelAdapter.from_traces("memory/traces", session_id="default")
    """

    def __init__(self, responses=None, default=None, stream_chunks=None,
                 raise_on_call=None):
        self._responses = list(responses) if responses else []
        self._default = default or FakeResponse()
        self._stream_chunks = list(stream_chunks) if stream_chunks else []
        self._raise_on_call = raise_on_call
        self._call_count = 0
        self._last_messages = None
        self._last_tools = None

    async def chat_complete(self, messages, tools=None, max_tokens=None):
        """Async call — returns FakeResponse."""
        if self._raise_on_call:
            raise self._raise_on_call
        self._call_count += 1
        self._last_messages = messages
        self._last_tools = tools
        if self._responses:
            return self._responses.pop(0)
        return self._default

    async def chat_stream_full(self, messages, tools=None):
        """Async streaming call — yields chunk dicts."""
        if self._raise_on_call:
            raise self._raise_on_call
        self._call_count += 1
        self._last_messages = messages
        self._last_tools = tools
        if self._stream_chunks:
            for chunk in self._stream_chunks:
                yield chunk
        elif self._default.content:
            yield {"type": "chunk", "content": self._default.content}
            if self._default.reasoning:
                yield {"type": "chunk", "content": "", "reasoning": self._default.reasoning}
        for tc in self._default.tool_calls:
            import json
            yield {"type": "tool_call", "name": tc["name"],
                   "arguments": json.dumps(tc.get("params", {})),
                   "id": tc.get("id", "call_0")}

    # --- Assertion helpers ---
    @property
    def call_count(self):
        return self._call_count

    @property
    def last_messages(self):
        return self._last_messages or []

    @property
    def last_tools(self):
        return self._last_tools or []

    def reset(self):
        self._call_count = 0
        self._last_messages = None
        self._last_tools = None

    # --- Factory ---
    @classmethod
    def from_traces(cls, trace_dir, session_id="default"):
        """Build responses from real model_call_end events in trace files."""
        import json
        from pathlib import Path

        trace_file = Path(trace_dir) / f"{session_id}.jsonl"
        if not trace_file.exists():
            return cls(default=FakeResponse(content="trace not found"))

        with open(trace_file, encoding="utf-8") as f:
            events = json.load(f)

        responses = []
        for e in events:
            if e.get("type") == "model_call_end":
                d = e.get("data", {})
                content = d.get("content", "")
                usage = d.get("usage", {})
                if content:
                    responses.append(FakeResponse(
                        content=content,
                        usage=usage,
                        reasoning=d.get("reasoning", ""),
                    ))
        return cls(responses=responses, default=FakeResponse(content="ok"))
