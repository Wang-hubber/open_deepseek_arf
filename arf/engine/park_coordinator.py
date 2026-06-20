"""ParkCoordinator — DEPRECATED: park/resume is built into AgentHarness (arf.harness.engine).

  register(state, type, metadata) -> wait_id
  complete(state, wait_id, result) -> bool
  park_round(state, cancel_event) -> str | None

Conditions are persisted in state._park_conditions so resume can
rebuild asyncio.Event instances via rebuild_events().
"""
from __future__ import annotations
import warnings
warnings.warn("ParkCoordinator is deprecated. Park/resume is built into AgentHarness.", DeprecationWarning, stacklevel=2)

import asyncio
import logging
from typing import TypedDict
from uuid import uuid4

logger = logging.getLogger("arf.engine.park")


class ParkCondition(TypedDict, total=False):
    status: str        # "pending" | "completed"
    type: str          # "hitl" | "subagent" | "peer"
    metadata: dict     # type-specific context


class ParkCoordinator:
    """Registry of wait conditions for session_park.

    Each condition is an asyncio.Event persisted in state._park_conditions.
    park_round() waits on all pending events concurrently and returns
    when the first one completes.
    """

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    # ── register ──

    async def register(
        self, state: dict, condition_type: str, metadata: dict
    ) -> str:
        """Register a park condition. Returns wait_id for downstream consumption."""
        wait_id = f"{condition_type}_{uuid4().hex[:8]}"
        condition: ParkCondition = {
            "status": "pending",
            "type": condition_type,
            "metadata": metadata,
        }
        state.setdefault("_park_conditions", {})[wait_id] = condition
        self._events[wait_id] = asyncio.Event()
        logger.debug("Park registered: %s type=%s", wait_id, condition_type)
        return wait_id

    # ── complete ──

    async def complete(
        self, state: dict, wait_id: str, result: dict
    ) -> bool:
        """Complete a park condition. Injects result into state, sets Event.

        Returns True if the condition was pending and is now completed.
        Returns False for nonexistent or already-completed conditions (idempotent).
        """
        conditions = state.get("_park_conditions", {})
        if wait_id not in conditions:
            return False
        cond = conditions[wait_id]
        if cond["status"] != "pending":
            return False

        self._inject_result(state, cond["type"], result)
        cond["status"] = "completed"

        event = self._events.get(wait_id)
        if event:
            event.set()
        logger.debug("Park completed: %s type=%s", wait_id, cond["type"])
        return True

    # ── park_round ──

    async def park_round(
        self,
        state: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> str | None:
        """Wait for any pending condition.

        Returns the completed wait_id, or None if no conditions to wait for
        or cancelled.
        """
        conditions = state.get("_park_conditions", {})
        pending_ids: list[str] = [
            wid for wid, c in conditions.items()
            if c["status"] == "pending"
        ]
        if not pending_ids:
            return None

        # Ensure events exist for all pending (covers resume path)
        wait_tasks: list[asyncio.Task] = []
        for wid in pending_ids:
            if wid not in self._events:
                self._events[wid] = asyncio.Event()
            wait_tasks.append(asyncio.create_task(self._events[wid].wait()))

        if cancel_event is not None:
            wait_tasks.append(asyncio.create_task(cancel_event.wait()))

        logger.debug("Park waiting on %d conditions", len(pending_ids))
        done, _ = await asyncio.wait(
            wait_tasks, return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks
        for t in wait_tasks:
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        # Check cancel first
        if cancel_event is not None and cancel_event.is_set():
            return None

        # Find which condition completed
        for wid in pending_ids:
            event = self._events.get(wid)
            if event and event.is_set():
                return wid

        return None

    # ── rebuild_events (resume) ──

    def rebuild_events(self, state: dict) -> None:
        """Rebuild asyncio.Event for all pending conditions from state.

        Called on resume when in-memory Event instances were lost.
        """
        for wid, cond in state.get("_park_conditions", {}).items():
            if cond["status"] == "pending":
                self._events[wid] = asyncio.Event()
                logger.debug("Park event rebuilt: %s", wid)

    # ── result injection ──

    def _inject_result(
        self, state: dict, condition_type: str, result: dict
    ) -> None:
        """Inject result into state.messages based on condition type."""
        if condition_type == "hitl":
            state.setdefault("messages", []).append({
                "role": "user",
                "content": result.get("answer", ""),
            })
            state.pop("_pending_human_decision", None)
            state["_primitive_result"] = None

        elif condition_type == "subagent":
            content = result.get("content", "")
            state.setdefault("messages", []).append({
                "role": "user",
                "content": content,
            })

        elif condition_type == "peer":
            content = result.get("content", "")
            state.setdefault("messages", []).append({
                "role": "system",
                "content": content,
            })

        # Clear park condition metadata from state if needed
        # (the condition stays _park_conditions but status=completed)
