"""HITLProtocol — engine interface for human-in-the-loop input requests."""
from __future__ import annotations

import asyncio
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

    def get_response_event(self, request_id: str) -> asyncio.Event | None:
        """Return the Event that is set when a response is provided."""
        ...

    def get_response(self, request_id: str) -> str:
        """Return the stored response for *request_id*, or empty string."""
        ...


class DefaultHITL:
    """EventBus-driven HITL with async notification for session parking.

    provide_response() sets an asyncio.Event so parked sessions can
    wake up and continue without session_end.

    When *park_coordinator* is provided, HITL conditions are registered
    through it instead of managing Events independently.
    """

    def __init__(self, event_bus, state_store=None,
                 park_coordinator=None) -> None:
        self._event_bus = event_bus
        self._state_store = state_store
        self._park_coordinator = park_coordinator
        self._answers: dict[str, str] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._wait_ids: dict[str, str] = {}  # request_id -> park wait_id

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

        if self._park_coordinator is not None:
            # New path: register via ParkCoordinator
            wait_id = await self._park_coordinator.register(
                ctx.state, "hitl",
                metadata={
                    "request_id": request_id,
                    "question": question,
                },
            )
            self._wait_ids[request_id] = wait_id
        else:
            # Legacy path: manage Event internally
            self._answers[request_id] = ""
            self._events[request_id] = asyncio.Event()

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
        if self._park_coordinator is not None:
            wait_id = self._wait_ids.get(request_id)
            if wait_id is None:
                return False
            # Load state to pass to complete()
            state = None
            if self._state_store:
                # Extract session_id from request_id: "sid_round_hex"
                parts = request_id.rsplit("_", 2)
                sid = "_".join(parts[:-2]) if len(parts) >= 3 else parts[0]
                state = await self._state_store.get(sid)
            if state is None:
                return False
            ok = await self._park_coordinator.complete(
                state, wait_id,
                {"answer": response},
            )
            if ok:
                await self._state_store.put(
                    state.get("session_id", ""), state)
            # Still emit event for frontend
            if self._event_bus:
                self._event_bus.emit(AgentEvent(
                    type="human_input_provided",
                    data={"request_id": request_id, "response": response},
                ))
            return ok

        # Legacy path
        if request_id not in self._answers:
            return False
        self._answers[request_id] = response
        event = self._events.get(request_id)
        if event:
            event.set()
        if self._event_bus:
            self._event_bus.emit(AgentEvent(
                type="human_input_provided",
                data={"request_id": request_id, "response": response},
            ))
        return True

    def get_response_event(self, request_id: str) -> asyncio.Event | None:
        """Return the Event for *request_id*.

        When using ParkCoordinator, the event lives in the coordinator,
        not in self._events. This method bridges the gap for session_park
        compatibility (used by PeerTeamPlugin legacy HITL branch).
        """
        wait_id = self._wait_ids.get(request_id)
        if wait_id is not None and self._park_coordinator is not None:
            return self._park_coordinator._events.get(wait_id)
        return self._events.get(request_id)

    def rebuild_from_state(self, state: dict) -> None:
        """Rebuild internal state from persisted state dict for resume support.

        When ParkCoordinator is active, this rebuilds asyncio.Event
        instances for pending park conditions and restores the
        _wait_ids mapping from persisted metadata.
        """
        if self._park_coordinator is not None:
            self._park_coordinator.rebuild_events(state)
        # Rebuild _wait_ids from park conditions of type "hitl"
        for wait_id, cond in state.get("_park_conditions", {}).items():
            if cond.get("type") == "hitl" and cond.get("status") == "pending":
                request_id = cond.get("metadata", {}).get("request_id")
                if request_id:
                    self._wait_ids[request_id] = wait_id

    def get_response(self, request_id: str) -> str:
        return self._answers.get(request_id, "")

    async def cancel_request(self, request_id: str) -> bool:
        self._events.pop(request_id, None)
        return self._answers.pop(request_id, None) is not None
