"""ARF Default Assistant — FastAPI server with lazy persistence.

Routes are split by concern into routers/:
  chat.py, trace.py, config.py, resources.py, misc.py
"""
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from agent_main import app_context

# ---- Logging setup ----
app_context.logs_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(app_context.logs_dir / "server.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("arf-assistant")

sys.path.insert(0, str(app_context.root))

from arf.agent.factory import create_agent
from arf.agent.config import AgentConfig

from routers import state


def _load_dotenv() -> None:
    env_path = app_context.root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- STARTUP ----
    _load_dotenv()
    cfg = AgentConfig.from_yaml(str(app_context.config_path))
    state._agent = create_agent(config=cfg, app_context=app_context)

    # Restore state
    s = await state._agent.state_store.get("default")
    if s:
        logger.info(f"Restored state: {len(s.get('messages', []))} messages, turn {s.get('current_turn', 0)}")

    logger.info(f"Agent '{cfg.name}' ready")
    await state._agent.start()
    yield
    # ---- SHUTDOWN ----
    if state._agent:
        await state._agent.stop()
    logger.info("Goodbye")


# ---- App ----
app = FastAPI(title="ARF Assistant", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Routers ----
from routers.chat import router as chat_router
from routers.trace import router as trace_router
from routers.config import router as config_router
from routers.resources import router as resources_router
from routers.misc import router as misc_router

app.include_router(chat_router)
app.include_router(trace_router)
app.include_router(config_router)
app.include_router(resources_router)
app.include_router(misc_router)

# ---- Static files + SPA fallback ----
frontend_dir = app_context.root.parent / "web" / "dist"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        return FileResponse(frontend_dir / "index.html")


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
