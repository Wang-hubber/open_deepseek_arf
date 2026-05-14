"""FastAPI web server -- REST API, WebSocket endpoint, and hot-reload watcher.

Single-user mode: ARFServer(workspace_dir=...)
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .routes import router, set_manager
from .session_manager import SessionManager
from .ws import WSHandler

_LOGGING_CONFIGURED = False


def _setup_logging():
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    _LOGGING_CONFIGURED = True


_setup_logging()
logger = logging.getLogger("arf.server")

DEBOUNCE_SECONDS = 1.0


class ARFServer:
    """Single-user ARF web server."""

    def __init__(self, workspace_dir: str):
        ws = Path(workspace_dir).resolve()
        self._workspace_dir = ws

        # Initialize trace database
        data_dir = Path(__file__).parent.parent.parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_name = os.environ.get("ARF_DB_NAME", "arf.db")
        init_db(str(data_dir / db_name))

        # Single SessionManager for the workspace
        self._mgr = SessionManager(ws)
        set_manager(self._mgr)

        self.app = FastAPI(title="ARF", lifespan=self._lifespan)
        self.app.include_router(router)

        # CORS
        cors_origins = os.environ.get("ARF_CORS_ORIGINS", "").split(",")
        cors_origins = [o.strip() for o in cors_origins if o.strip()]
        if not cors_origins:
            cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self._setup_ws()
        if os.environ.get("ARF_SERVE_STATIC", "1") != "0":
            self._setup_static()

    def _setup_static(self):
        static_dir = Path(__file__).parent / "static"
        if static_dir.exists():
            self.app.mount(
                "/",
                StaticFiles(directory=str(static_dir), html=True),
                name="static",
            )

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        watcher_task = asyncio.create_task(self._watch_workspace())
        try:
            yield
        finally:
            if hasattr(self, "_stop_watch"):
                self._stop_watch.set()
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

    def _setup_ws(self):
        ws_handler = WSHandler(self._mgr)

        @self.app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket, token: str = Query("")):
            await websocket.accept()
            await ws_handler.on_connect(websocket)
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                await ws_handler.on_disconnect(websocket)

    async def _watch_workspace(self):
        from watchfiles import awatch

        ws = self._workspace_dir
        watch_dirs = [ws / d for d in ("tools", "skills")]
        models_dir = ws / "models"
        if models_dir.exists():
            watch_dirs.append(models_dir)

        watch_dirs = [d for d in watch_dirs if d.exists()]
        if not watch_dirs:
            return

        self._stop_watch = asyncio.Event()
        logger.info("Hot-reload watcher started on %s", [str(d) for d in watch_dirs])

        try:
            async for changes in awatch(*watch_dirs):
                if self._stop_watch.is_set():
                    break
                await asyncio.sleep(DEBOUNCE_SECONDS)
                if self._stop_watch.is_set():
                    break

                registry = self._mgr.get_registry()
                if registry is None:
                    continue
                changed = registry.reload_user(str(ws))
                if changed:
                    logger.info("Hot-reload: %s", ", ".join(changed))
        except Exception:
            logger.exception("Hot-reload watcher error")
        finally:
            logger.info("Hot-reload watcher stopped")

    def start(self, host: str = "127.0.0.1", port: int = 8000):
        import uvicorn
        uvicorn.run(self.app, host=host, port=port)
