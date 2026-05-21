"""WebSocket handler -- session lifecycle tracking with hook-triggered archiving."""

import asyncio
import logging
import shutil
from pathlib import Path

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
        was_reconnect = self._disconnect_task and not self._disconnect_task.done()
        if was_reconnect:
            self._disconnect_task.cancel()
            self._pending_session = None
            logger.info("WS reconnect -- cancelled pending disconnect (network blip)")
        self._connections.add(websocket)
        logger.info("WS connect (sessions: %d)", len(self._connections))

        # Trigger SessionStart hook on first connection only
        if not was_reconnect:
            try:
                loop = asyncio.get_running_loop()
                runner = self._mgr.get_hook_runner()
                await loop.run_in_executor(
                    None,
                    lambda: runner.run("SessionStart", {
                        "session_id": self._mgr.current_session_id,
                        "session_title": self._mgr.session_title,
                    }),
                )
            except Exception:
                logger.exception("SessionStart hooks failed")

    async def on_disconnect(self, websocket):
        self._connections.discard(websocket)
        logger.info("WS disconnect (sessions: %d)", len(self._connections))

        if self._connections:
            logger.info("Another tab still connected, skipping archive")
            return

        if not self._mgr.session_history or len(self._mgr.session_history) < 2:
            # Clean up tentative session directory created by SessionStart's
            # system_log hook — this session had no user messages.
            sid = self._mgr.current_session_id
            if sid != SessionManager.SYSTEM_SESSION_ID:
                session_dir = Path(str(self._mgr.workspace_dir)) / "memory" / "sessions" / sid
                if session_dir.exists():
                    shutil.rmtree(session_dir)
                # Soft-delete DB record if one was created
                try:
                    from .database import delete_session_db
                    delete_session_db(sid)
                except Exception:
                    pass
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

        # Trigger SessionEnd via the unified fire_session_end path.
        # fire_session_end is idempotent — if already fired (e.g. by the
        # streaming "done" handler), it returns early.
        already_fired = self._mgr._session_end_fired
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self._mgr.fire_session_end(trigger="ws_disconnect"),
            )
        except Exception:
            logger.exception("SessionEnd hooks failed")

        # If SessionEnd was already handled by another path (streaming / HTTP),
        # don't create a duplicate DB record with a different session_id.
        if already_fired:
            logger.info("SessionEnd already handled; skipping duplicate DB record")
            self._mgr.reset_session_history()
            return

        session_id = start_time.strftime("%Y%m%d_%H%M%S_%f")
        filepath = f"memory/sessions/{session_id}/archive.json"

        try:
            from .database import insert_session, update_session, save_session_cost
            from pathlib import Path
            insert_session(session_id, "admin", title, filepath)
            fpath = Path(str(self._mgr.workspace_dir)) / filepath
            if fpath.exists():
                sz = fpath.stat().st_size / (1024 * 1024)
                turns = len(history) // 2
                update_session(session_id, turn_count=turns, json_size_mb=round(sz, 3), message_count=len(history))
            if self._mgr.last_usage:
                model_breakdown = {}
                for t in (self._mgr.last_traces or []):
                    m = t.get("model")
                    if m:
                        model_breakdown[m] = model_breakdown.get(m, 0) + t.get("total_tokens", 0)
                save_session_cost(
                    session_id,
                    total_tokens=self._mgr.last_usage.get("total_tokens", 0),
                    prompt_tokens=self._mgr.last_usage.get("prompt_tokens", 0),
                    completion_tokens=self._mgr.last_usage.get("completion_tokens", 0),
                    model_breakdown=model_breakdown,
                )
        except Exception:
            logger.exception("Failed to write session DB record")

        self._mgr.reset_session_history()
