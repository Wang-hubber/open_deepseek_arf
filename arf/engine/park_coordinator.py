"""ParkCoordinator — thin stub. Park/resume is built into AgentHarness."""
from __future__ import annotations
import asyncio
import logging
from uuid import uuid4

logger = logging.getLogger("arf.engine.park")


class ParkCoordinator:
    """Legacy park coordinator stub. Park/resume is built into AgentHarness."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    async def register(self, state: dict, condition_type: str, metadata: dict) -> str:
        wait_id = f"{condition_type}_{uuid4().hex[:8]}"
        state.setdefault("_park_conditions", {})[wait_id] = {
            "status": "pending", "type": condition_type, "metadata": metadata,
        }
        self._events[wait_id] = asyncio.Event()
        return wait_id

    async def complete(self, state: dict, wait_id: str, result: dict) -> bool:
        conditions = state.get("_park_conditions", {})
        if wait_id not in conditions or conditions[wait_id].get("status") != "pending":
            return False
        conditions[wait_id]["status"] = "completed"
        event = self._events.get(wait_id)
        if event:
            event.set()
        return True

    async def park_round(self, state: dict, cancel_event: asyncio.Event | None = None) -> str | None:
        conditions = state.get("_park_conditions", {})
        pending = [wid for wid, c in conditions.items() if c.get("status") == "pending"]
        if not pending:
            return None
        tasks = [asyncio.create_task(self._events.setdefault(wid, asyncio.Event()).wait()) for wid in pending]
        if cancel_event:
            tasks.append(asyncio.create_task(cancel_event.wait()))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            if not t.done():
                t.cancel()
        for wid in pending:
            evt = self._events.get(wid)
            if evt and evt.is_set():
                return wid
        return None

    def rebuild_events(self, state: dict) -> None:
        for wid, cond in state.get("_park_conditions", {}).items():
            if cond.get("status") == "pending":
                self._events[wid] = asyncio.Event()
