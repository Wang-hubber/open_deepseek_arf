"""HITLProtocol — engine interface for human-in-the-loop input requests."""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable
from uuid import uuid4

from arf.core.events import AgentEvent
from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.hitl")


@runtime_checkable
class HITLProtocol(Protocol):
    """Protocol for human-in-the-loop input requests.

    Called by the engine when a tool returns pending=True.
    Implementations decide HOW to collect the human response.
    """

    async def request_input(
        self, question: str, options: list[str], context: str,
        task_id: str, deadline: float, ctx: PluginContext,
    ) -> dict:
        """Request human input. Returns {"request_id": str, "status": "pending"}."""
        ...

    async def provide_response(self, request_id: str, response: str) -> bool:
        """Provide answer to a pending request. Returns True if fulfilled."""
        ...

    async def cancel_request(self, request_id: str) -> bool:
        """Cancel a pending request."""
        ...


class DefaultHITL:
    """EventBus-driven HITL. App layer calls provide_response() then
    injects the answer as a new user message for the next round."""

    def __init__(self, event_bus, state_store=None) -> None:
        self._event_bus = event_bus
        self._state_store = state_store
        self._answers: dict[str, str] = {}

    async def request_input(
        self, question: str, options: list[str], context: str,
        task_id: str, deadline: float, ctx: PluginContext,
    ) -> dict:
        request_id = f"{ctx.session_id}_{ctx.interaction_round}_{uuid4().hex[:8]}"
        ctx.state["_pending_human_decision"] = {
            "request_id": request_id, "question": question,
            "options": options, "context": context,
            "task_id": task_id, "deadline": deadline,
        }
        self._answers[request_id] = ""
        if self._event_bus:
            self._event_bus.emit(AgentEvent(
                type="need_human_input",
                data={
                    "request_id": request_id, "session_id": ctx.session_id,
                    "question": question, "options": options,
                    "context": context, "task_id": task_id, "deadline": deadline,
                },
                session_id=ctx.session_id,
            ))
        logger.info("need_human_input: sid=%s round=%s q=%s",
                     ctx.session_id, ctx.interaction_round, question[:80])
        return {"request_id": request_id, "status": "pending"}

    async def provide_response(self, request_id: str, response: str) -> bool:
        if request_id not in self._answers:
            return False
        self._answers[request_id] = response
        if self._event_bus:
            self._event_bus.emit(AgentEvent(
                type="human_input_provided",
                data={"request_id": request_id, "response": response},
            ))
        return True

    async def cancel_request(self, request_id: str) -> bool:
        return self._answers.pop(request_id, None) is not None
