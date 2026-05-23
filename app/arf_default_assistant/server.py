"""ARF Default Assistant -- FastAPI server with lazy persistence."""
import json
import logging
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---- Logging setup ----
Path("logs").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/server.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("arf-assistant")

# Add project root and CWD to path so imports work
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from arf.agent.factory import create_agent
from arf.agent.config import AgentConfig
from arf.core.state import AgentState

_agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global _agent

    # ---- STARTUP ----
    cfg = AgentConfig.from_yaml("agent.yaml")
    _agent = create_agent(config=cfg)

    from lazy_persistence import load_archive
    archive = load_archive()
    if archive:
        state: AgentState = {
            "session_id": "default",
            "agent_name": cfg.name,
            "messages": archive.get("messages", []),
            "current_model": cfg.models[0].name if cfg.models else "default",
            "current_turn": archive.get("current_turn", 0),
            "context_summary": archive.get("context_summary", ""),
            "tool_results": {},
            "plan": None,
            "metadata": archive.get("metadata", {}),
        }
        await _agent.state_store.put("default", state)
        logger.info(f"Restored state: {len(state['messages'])} messages, turn {state['current_turn']}")

    from arf.observability import FileTraceStore
    FileTraceStore(_agent.event_bus, dir="./memory/sessions")

    logger.info(f"Agent '{cfg.name}' ready")
    yield
    # ---- SHUTDOWN ----
    logger.info("Shutting down...")
    from lazy_persistence import save_archive_async
    if _agent:
        await save_archive_async(_agent)
    logger.info("Goodbye")


app = FastAPI(title="ARF Assistant", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
signal.signal(signal.SIGINT, lambda *a: sys.exit(0))


class ChatReq(BaseModel):
    message: str


@app.post("/api/chat")
async def chat(req: ChatReq):
    result = await _agent.chat(req.message)
    return JSONResponse({"content": result})


@app.get("/api/chat/stream")
async def chat_stream(message: str = Query(...)):
    import asyncio as _aio

    async def gen():
        bus = _agent.event_bus
        queue: _aio.Queue = _aio.Queue()
        async def _collect():
            async for event in bus.subscribe():
                await queue.put(event)
        collector = _aio.create_task(_collect())
        chat_task = _aio.create_task(_agent.chat(message))
        try:
            while not chat_task.done():
                try:
                    event = await _aio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {json.dumps({'type': event.type, 'data': event.data, 'timestamp': event.timestamp, 'turn': event.turn}, ensure_ascii=False)}\n\n"
                except _aio.TimeoutError:
                    pass
            while not queue.empty():
                event = queue.get_nowait()
                yield f"data: {json.dumps({'type': event.type, 'data': event.data, 'timestamp': event.timestamp, 'turn': event.turn}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}})}\n\n"
        finally:
            collector.cancel()
            yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/trace")
async def get_trace():
    trace_dir = Path("./memory/sessions")
    events = []
    if trace_dir.exists():
        for p in sorted(trace_dir.glob("*.json")):
            try:
                events.extend(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
    return JSONResponse({"events": events})


@app.get("/api/trace/stream")
async def trace_stream():
    async def gen():
        try:
            async for event in _agent.event_bus.subscribe():
                yield (
                    f"data: {json.dumps({'type': event.type, 'data': event.data, 'turn': event.turn}, ensure_ascii=False)}\n\n"
                )
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/config/register-deepseek")
async def config_register_deepseek(req: dict):
    """Accept DeepSeek API key, register models (frontend ConfigPage flow)."""
    api_key = (req or {}).get("api_key", "").strip()
    if not api_key:
        return JSONResponse({"error": "API key required"}, status_code=400)
    # Models already configured in agent.yaml — just confirm
    return JSONResponse({
        "ok": True,
        "action": "register_deepseek",
        "models_created": [m.name for m in _agent.config.models],
        "models": [{"name": m.name, "model": m.model} for m in _agent.config.models],
    })

@app.get("/api/config/status")
async def config_status():
    cfg = _agent.config
    return JSONResponse({
        "name": cfg.name,
        "description": cfg.description,
        "models": [m.name for m in cfg.models],
        "tool_count": len(cfg.tools),
        "skill_count": len(cfg.skills),
        "hook_count": len(cfg.hooks),
        "sub_agents": [a.name for a in (cfg.agents or [])],
        "advanced": {
            "loop_strategy": cfg.effective_advanced().loop_strategy,
        },
    })


@app.get("/api/resources/{res_type}")
async def list_resources(res_type: str):
    if res_type == "tools":
        items = [
            {"name": t.name, "description": t.description, "activation": t.activation}
            for t in _agent.config.tools
        ]
    elif res_type == "skills":
        items = [
            {"name": s.name, "description": s.description, "tools": s.tools}
            for s in _agent.config.skills
        ]
    else:
        return JSONResponse({"error": f"unknown type: {res_type}"}, status_code=400)
    return JSONResponse({"type": res_type, "items": items, "count": len(items)})


@app.post("/api/save")
async def manual_save():
    from lazy_persistence import save_archive_async
    await save_archive_async(_agent)
    return JSONResponse({"status": "saved"})


@app.get("/api/archive")
async def download_archive():
    p = Path("memory/archive.json")
    if p.exists():
        return FileResponse(p, media_type="application/json")
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/reload")
async def reload_config():
    """Reload agent config and reinitialize the agent."""
    global _agent
    if _agent:
        from lazy_persistence import save_archive_async
        await save_archive_async(_agent)
    cfg = AgentConfig.from_yaml("agent.yaml")
    _agent = create_agent(config=cfg)
    return JSONResponse({"status": "reloaded", "name": cfg.name})


class FeedbackReq(BaseModel):
    rating: int = 0
    comment: str = ""


@app.post("/api/feedback")
async def feedback(req: FeedbackReq):
    log = Path("memory/feedback.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(json.dumps({"timestamp": time.time(), **req.model_dump()}) + "\n")
    return JSONResponse({"status": "recorded"})


@app.get("/api/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "agent": _agent.config.name if _agent else "not initialized",
    })

# ---- Session stubs (single infinite session — no session management) ----
@app.get("/api/sessions")
async def sessions_list():
    return JSONResponse([])

@app.post("/api/sessions")
async def sessions_create():
    return JSONResponse({"session_id": "default", "title": "ARF Assistant"})

@app.get("/api/sessions/active")
async def sessions_active():
    return JSONResponse({"session_id": "default", "title": "ARF Assistant"})

# ---- WebSocket stub (single session — no real-time sync needed) ----
from fastapi import WebSocket
@app.websocket("/ws")
async def ws_stub(ws: WebSocket):
    await ws.accept()
    await ws.close()

# Static files (frontend) -- if built
frontend_dir = Path("../web/dist")
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
