"""ARF Default Assistant -- FastAPI server with lazy persistence."""
import json
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
        print(f"[server] Restored state: {len(state['messages'])} messages, turn {state['current_turn']}")

    from arf.observability import FileTraceStore
    FileTraceStore(_agent.event_bus, dir="./memory/sessions")

    print(f"[server] Agent '{cfg.name}' ready")
    yield
    # ---- SHUTDOWN ----
    print("[server] Shutting down...")
    from lazy_persistence import save_archive_async
    if _agent:
        await save_archive_async(_agent)
    print("[server] Goodbye")


app = FastAPI(title="ARF Assistant", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
signal.signal(signal.SIGINT, lambda *a: sys.exit(0))


class ChatReq(BaseModel):
    message: str


@app.post("/chat")
async def chat(req: ChatReq):
    result = await _agent.chat(req.message)
    return JSONResponse({"content": result})


@app.get("/chat/stream")
async def chat_stream(message: str = Query(...)):
    async def gen():
        try:
            async for event in _agent.astream(message):
                yield (
                    f"data: {json.dumps({'type': event.type, 'data': event.data, 'timestamp': event.timestamp, 'turn': event.turn}, ensure_ascii=False)}\n\n"
                )
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/trace")
async def get_trace():
    trace_dir = Path("./memory/sessions")
    sessions = {}
    if trace_dir.exists():
        for p in sorted(trace_dir.glob("*.json")):
            try:
                sessions[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                sessions[p.stem] = []
    return JSONResponse({"sessions": sessions})


@app.get("/trace/stream")
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


@app.get("/config/status")
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


@app.get("/resources/{res_type}")
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


@app.post("/save")
async def manual_save():
    from lazy_persistence import save_archive_async
    await save_archive_async(_agent)
    return JSONResponse({"status": "saved"})


@app.get("/archive")
async def download_archive():
    p = Path("memory/archive.json")
    if p.exists():
        return FileResponse(p, media_type="application/json")
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/reload")
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


@app.post("/feedback")
async def feedback(req: FeedbackReq):
    log = Path("memory/feedback.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(json.dumps({"timestamp": time.time(), **req.model_dump()}) + "\n")
    return JSONResponse({"status": "recorded"})


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "agent": _agent.config.name if _agent else "not initialized",
    })


# Static files (frontend) -- if built
frontend_dir = Path("../web/dist")
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
