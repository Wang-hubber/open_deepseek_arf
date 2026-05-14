"""WebSocket handler -- session lifecycle tracking with hook-triggered archiving."""

import asyncio
import logging

from .session_manager import SessionManager

logger = logging.getLogger(__name__)

GRACE_PERIOD_SECONDS = 15


class WSHandler:
    """Tracks WebSocket connections and triggers session archiving on disconnect."""

    def __init__(self, mgr: SessionManager):
        self._mgr = mgr
        self._connections: set = set()
        self._disconnect_task: asyncio.Task | None = None
        self._pending_session: dict | None = None

    async def on_connect(self, websocket):
        if self._disconnect_task and not self._disconnect_task.done():
            self._disconnect_task.cancel()
            self._pending_session = None
            logger.info("WS reconnect -- cancelled pending disconnect (network blip)")
        self._connections.add(websocket)
        logger.info("WS connect (sessions: %d)", len(self._connections))

    async def on_disconnect(self, websocket):
        self._connections.discard(websocket)
        logger.info("WS disconnect (sessions: %d)", len(self._connections))

        if self._connections:
            logger.info("Another tab still connected, skipping archive")
            return

        if not self._mgr.session_history or len(self._mgr.session_history) < 2:
            self._mgr.reset_session_history()
            return

        self._pending_session = {
            "history": list(self._mgr.session_history),
            "start_time": self._mgr.session_start_time,
            "title": self._mgr.session_title,
        }
        self._disconnect_task = asyncio.create_task(self._deferred_disconnect())

    async def _deferred_disconnect(self):
        try:
            await asyncio.sleep(GRACE_PERIOD_SECONDS)
        except asyncio.CancelledError:
            return

        data = self._pending_session
        self._pending_session = None
        if data is None:
            return

        history = data["history"]
        start_time = data["start_time"]
        title = data["title"]

        if self._mgr.session_start_time != start_time:
            logger.info("Session already handled by another path, skipping archive")
            return

        session_id = start_time.strftime("%Y%m%d_%H%M%S")
        try:
            loop = asyncio.get_running_loop()
            runner = self._mgr.get_hook_runner()
            await loop.run_in_executor(
                None,
                lambda: runner.run("SessionEnd", {
                    "session_id": session_id,
                    "session_title": title,
                }, stdin_data={
                    "conversation": history,
                    "session_start": start_time.isoformat(),
                    "message_count": len(history),
                }),
            )
        except Exception:
            logger.exception("SessionEnd hooks failed")

        try:
            from .database import insert_session, update_session
            from pathlib import Path
            insert_session(session_id, "admin", title, f"memory/sessions/{session_id}.json")
            fpath = Path(str(self._mgr.workspace_dir)) / "memory" / "sessions" / f"{session_id}.json"
            if fpath.exists():
                sz = fpath.stat().st_size / (1024 * 1024)
                turns = len(history) // 2
                update_session(session_id, turn_count=turns, json_size_mb=round(sz, 3), message_count=len(history))
        except Exception:
            logger.exception("Failed to write session DB record")

        self._mgr.reset_session_history()
