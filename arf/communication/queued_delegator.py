"""QueuedTaskDelegator — FIFO task queue with per-session concurrency slots."""
import asyncio
import logging
from typing import Callable, Awaitable
from dataclasses import dataclass, field

logger = logging.getLogger("arf.communication.queued_delegator")


@dataclass
class _RunningEntry:
    task_id: str
    task: dict


@dataclass
class _QueuedEntry:
    task_id: str
    task: dict
    runner: Callable[[dict], Awaitable[dict]]


@dataclass
class _SessionSlots:
    max_concurrent: int
    running: dict[str, _RunningEntry] = field(default_factory=dict)
    queue: list[_QueuedEntry] = field(default_factory=list)
    completed: list[dict] = field(default_factory=list)


class QueuedTaskDelegator:
    """FIFO task queue with per-session concurrency-limited slot scheduling.

    App injects ``runner`` callbacks via :meth:`dispatch`. The framework owns
    slot allocation, FIFO ordering, and result staging. App reports completion
    via :meth:`complete` when the sub-agent stream finishes.
    """

    def __init__(self, max_concurrent: int = 3) -> None:
        self._max_concurrent = max_concurrent
        self._task_id_counter = 0
        self._sessions: dict[str, _SessionSlots] = {}

    def _next_id(self) -> str:
        self._task_id_counter += 1
        return f"task_{self._task_id_counter}"

    def _get_or_create_session(self, session_id: str) -> _SessionSlots:
        if session_id not in self._sessions:
            self._sessions[session_id] = _SessionSlots(
                max_concurrent=self._max_concurrent,
            )
        return self._sessions[session_id]

    async def dispatch(
        self,
        session_id: str,
        task: dict,
        runner: Callable[[dict], Awaitable[dict]],
    ) -> dict:
        task_id = self._next_id()
        session = self._get_or_create_session(session_id)

        if len(session.running) < session.max_concurrent:
            session.running[task_id] = _RunningEntry(task_id=task_id, task=task)
            asyncio.create_task(self._run_wrapped(session_id, task_id, task, runner))
            await asyncio.sleep(0)  # yield to let the created task start
            return {"ok": True, "dispatched": True, "task_id": task_id}
        else:
            session.queue.append(_QueuedEntry(task_id=task_id, task=task, runner=runner))
            return {
                "ok": True,
                "queued": True,
                "task_id": task_id,
                "position": len(session.queue),
            }

    async def _run_wrapped(
        self,
        session_id: str,
        task_id: str,
        task: dict,
        runner: Callable[[dict], Awaitable[dict]],
    ) -> None:
        task["_delegator_task_id"] = task_id  # make task_id available to runner
        try:
            await runner(task)
        except Exception:
            logger.exception("Runner failed for task %s in session %s", task_id, session_id)
            await self.complete(
                session_id, task_id,
                {"ok": False, "error": f"Runner exception for task {task_id}"},
            )

    async def complete(
        self, session_id: str, task_id: str, result: dict
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return

        removed = session.running.pop(task_id, None)
        if removed is None:
            return  # already completed — idempotent, skip duplicate
        session.completed.append({**result, "task_id": task_id})

        if session.queue:
            next_entry = session.queue.pop(0)
            session.running[next_entry.task_id] = _RunningEntry(
                task_id=next_entry.task_id, task=next_entry.task
            )
            asyncio.create_task(
                self._run_wrapped(
                    session_id, next_entry.task_id, next_entry.task, next_entry.runner
                )
            )

    async def get_pending(self, session_id: str) -> list[dict]:
        """Return and clear all completed results for *session_id*. Consuming read."""
        session = self._sessions.get(session_id)
        if session is None:
            return []
        pending = list(session.completed)
        session.completed.clear()
        return pending

    async def get_task_result(self, session_id: str, task_id: str) -> dict | None:
        """Return a specific task result WITHOUT consuming it.

        Used by await_task for targeted polling — does not interfere with
        pre_action's get_pending() which consumes all results at once.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        for entry in session.completed:
            if entry.get("task_id") == task_id:
                return dict(entry)
        return None

    async def queue_status(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            return {"running": [], "queued": [], "max_concurrent": self._max_concurrent}

        return {
            "running": [
                {"task_id": e.task_id, "task": e.task}
                for e in session.running.values()
            ],
            "queued": [
                {"task_id": e.task_id, "task": e.task, "position": i + 1}
                for i, e in enumerate(session.queue)
            ],
            "max_concurrent": session.max_concurrent,
        }

    async def cancel(self, session_id: str, task_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False

        for i, entry in enumerate(session.queue):
            if entry.task_id == task_id:
                session.queue.pop(i)
                return True
        return False

    def reset(self) -> None:
        """Clear all sessions, tasks, and queues (test double support)."""
        self._task_id_counter = 0
        self._sessions.clear()
