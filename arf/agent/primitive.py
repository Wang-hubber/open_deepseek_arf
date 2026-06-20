"""PrimitiveAgent — 6 primitives: input, model_call, wait, finish_wait, stop, resume."""
from __future__ import annotations
import uuid
import time
from collections.abc import Callable, Awaitable
from typing import Any
from arf.agent.state import AgentState, Message, WaitItem, ModelResult


class PrimitiveAgent:
    """Passive message state machine with model calling capability.

    Knows nothing about tools, hooks, session/turn lifecycle, sandbox, or events.
    """

    def __init__(
        self,
        agent_id: str,
        model_config: dict,
        call_model: Callable[[list[dict], list[dict] | None], Awaitable[ModelResult]],
    ) -> None:
        self.state = AgentState(
            agent_id=agent_id,
            session_id="",           # assigned by harness when session starts
            messages=[],
            waiting={},
            model_config=model_config,
        )
        self._call_model = call_model
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

    async def model_call(self) -> ModelResult:
        """Single LLM API call consuming state.messages."""
        if not self._active:
            raise RuntimeError("Agent has been stopped")
        messages = [
            {"role": m.role, "content": m.content}
            for m in self.state.messages
        ]
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
    ) -> PrimitiveAgent:
        """Reconstruct agent from state, including model connection and session_id."""
        agent = cls(
            agent_id=state.agent_id,
            model_config=state.model_config,
            call_model=call_model,
        )
        agent.state = state      # restore full state including session_id + messages + waiting
        agent._active = True
        return agent
