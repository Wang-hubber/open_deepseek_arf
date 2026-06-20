"""PrimitiveAgent — 6 primitives: input, model_call, wait, finish_wait, stop, resume."""
from __future__ import annotations
import json
import uuid
import time
from collections.abc import Callable, Awaitable, AsyncIterator
from typing import Any
from arf.agent.state import AgentState, Message, WaitItem, ModelResult


class ModelStream:
    """AsyncIterator yielding raw model chunks. Aggregates .result after exhaustion.

    App consumes chunks directly (SSE, UI). Harness reads .result to update state.
    """

    def __init__(self, generator: AsyncIterator[dict]) -> None:
        self._gen = generator
        self._result: ModelResult | None = None
        self._content_parts: list[str] = []
        self._tool_calls: dict[str, dict] = {}
        self._usage: dict[str, int] = {}

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        try:
            chunk = await self._gen.__anext__()
        except StopAsyncIteration:
            if self._result is None:
                self._finalize()
            raise

        t = chunk.get("type", "")
        if t == "chunk":
            self._content_parts.append(chunk.get("content", ""))
        elif t == "tool_call":
            try:
                params = json.loads(chunk.get("arguments", "{}"))
            except json.JSONDecodeError:
                params = {}
            self._tool_calls.setdefault(chunk["id"], {
                "id": chunk["id"],
                "name": chunk["name"],
                "params": params,
            })
        elif t == "usage":
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if k in chunk:
                    self._usage[k] = chunk[k]

        return chunk

    def _finalize(self) -> None:
        tool_calls = list(self._tool_calls.values())
        self._result = ModelResult(
            content="".join(self._content_parts),
            tool_calls=tool_calls,
            usage=self._usage,
            finish_reason="tool_calls" if tool_calls else "stop",
        )

    @property
    def result(self) -> ModelResult:
        if self._result is None:
            self._finalize()
        return self._result


class PrimitiveAgent:
    """Passive message state machine with model calling capability.

    Knows nothing about tools, hooks, session/turn lifecycle, sandbox, or events.
    """

    def __init__(
        self,
        agent_id: str,
        model_config: dict,
        call_model: Callable[[list[dict], list[dict] | None], Awaitable[ModelResult]],
        stream_model: Callable[[list[dict], list[dict] | None], AsyncIterator[dict]] | None = None,
    ) -> None:
        self.state = AgentState(
            agent_id=agent_id,
            session_id="",           # assigned by harness when session starts
            messages=[],
            waiting={},
            model_config=model_config,
        )
        self._call_model = call_model
        self._stream_model = stream_model
        self._active = True

    # ── input ──────────────────────────────────────────

    def input(self, role: str, content: Any, position: str | int = "end") -> Message:
        """Inject a message into state.messages."""
        msg = Message(
            message_id=str(uuid.uuid4()),
            role=role,
            content=content,
        )
        if position == "end":
            self.state.messages.append(msg)
        elif position == "begin":
            self.state.messages.insert(0, msg)
        elif isinstance(position, int):
            self.state.messages.insert(position, msg)
        else:
            self.state.messages.append(msg)
        return msg

    # ── model_call ─────────────────────────────────────

    async def model_call(self, stream: bool = True):
        """Single LLM API call consuming state.messages.

        stream=True (default)  → ModelStream (yielded chunks + .result).
        stream=False           → ModelResult (aggregated).
        """
        if not self._active:
            raise RuntimeError("Agent has been stopped")
        messages = [
            {"role": m.role, "content": m.content}
            for m in self.state.messages
        ]
        if stream and self._stream_model:
            gen = self._stream_model(messages, None)
            return ModelStream(gen)
        return await self._call_model(messages, None)

    # ── wait ───────────────────────────────────────────

    def wait(self, hook_name: str, reason: str) -> WaitItem:
        """Append WaitItem to state.waiting[hook_name]. Synchronous, does not block."""
        wi = WaitItem(
            wait_id=str(uuid.uuid4()),
            hook_name=hook_name,
            reason=reason,
            created_at=time.time(),
        )
        self.state.waiting.setdefault(hook_name, []).append(wi)
        return wi

    # ── finish_wait ────────────────────────────────────

    def finish_wait(self, wait_id: str, reason: str = "") -> dict[str, list[WaitItem]]:
        """Remove WaitItem by id. Returns updated state.waiting."""
        for hook_name, items in list(self.state.waiting.items()):
            self.state.waiting[hook_name] = [wi for wi in items if wi.wait_id != wait_id]
            if not self.state.waiting[hook_name]:
                del self.state.waiting[hook_name]
        return self.state.waiting

    # ── stop ───────────────────────────────────────────

    def stop(self) -> AgentState:
        """Return current full state for persistence. Tears down model connection."""
        self._active = False
        return self.state

    # ── resume ─────────────────────────────────────────

    @classmethod
    def resume(
        cls, state: AgentState,
        call_model: Callable[[list[dict], list[dict] | None], Awaitable[ModelResult]],
        stream_model: Callable[[list[dict], list[dict] | None], AsyncIterator[dict]] | None = None,
    ) -> PrimitiveAgent:
        """Reconstruct agent from state, including model connection and session_id."""
        agent = cls(
            agent_id=state.agent_id,
            model_config=state.model_config,
            call_model=call_model,
            stream_model=stream_model,
        )
        agent.state = state      # restore full state including session_id + messages + waiting
        agent._active = True
        return agent
