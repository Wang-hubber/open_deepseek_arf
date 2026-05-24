"""ARF Default Assistant -- FastAPI server with lazy persistence."""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, UploadFile
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
from arf.agent.registry import set_agent
from arf.core.state import AgentState

_agent = None
_active_cancel_events: dict[str, asyncio.Event] = {}  # session_id → cancel event


def _load_dotenv() -> None:
    """Load .env file into os.environ (simple parser, no python-dotenv needed)."""
    env_path = Path(".env")
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
    """Startup and shutdown lifecycle."""
    global _agent

    # ---- STARTUP ----
    _load_dotenv()
    cfg = AgentConfig.from_yaml("agent.yaml")
    _agent = create_agent(config=cfg)
    set_agent(_agent)
    set_agent(_agent)

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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shutdown handled by FastAPI lifespan (cross-platform, no signal tricks)


class ChatReq(BaseModel):
    message: str
    stream: bool = False
    history: list[dict] | None = None
    new_session: bool = False


@app.post("/api/chat")
async def chat(req: ChatReq):
    if req.stream:
        return StreamingResponse(_sse_chat(req.message), media_type="text/event-stream")
    try:
        result = await _agent.chat(req.message)
        return JSONResponse({"content": result})
    except Exception as e:
        return JSONResponse({"content": "", "error": str(e)}, status_code=500)


async def _sse_chat(message: str):
    """Stream chat via framework agent.astream(), translate events to frontend format.

    Creates a cancel event and injects it into the engine so that
    POST /api/chat/cancel or client disconnect can stop the agent.
    """
    cancel_evt = asyncio.Event()
    _active_cancel_events["default"] = cancel_evt
    _agent._engine.set_cancel_event(cancel_evt)

    try:
        async for event in _agent.astream(message):
            t = event.type
            if t == "thinking_delta":
                chunk = {"type": "chunk", "content": event.data.get("content", "")}
                if event.data.get("reasoning"):
                    chunk["reasoning"] = event.data["reasoning"]
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            elif t == "tool_call_start":
                yield f"data: {json.dumps({'type': 'tool_call', 'name': event.data.get('tool_name', ''), 'arguments': event.data.get('arguments', '{}'), 'id': event.data.get('id', 'call_0')}, ensure_ascii=False)}\n\n"
            elif t == "tool_call_end":
                success = event.data.get("success", False)
                yield f"data: {json.dumps({'type': 'tool_result', 'id': event.data.get('id', event.data.get('tool_name', 'call_0')), 'result': 'success' if success else 'error', 'tool': event.data.get('tool_name', ''), 'content': event.data.get('result', '') if success else '', 'error_msg': event.data.get('error', '')}, ensure_ascii=False)}\n\n"
            elif t == "error":
                detail = event.data.get("detail", "API error")
                code = event.data.get("code", 0)
                yield f"data: {json.dumps({'type': 'error', 'detail': f'[{code}] {detail}'}, ensure_ascii=False)}\n\n"
                return
            elif t == "session_end":
                reason = event.data.get("reason", "")
                if reason == "cancelled":
                    yield f"data: {json.dumps({'type': 'cancelled'}, ensure_ascii=False)}\n\n"
                    return

        # Send done with FULL history for frontend renderFromHistory
        state = await _agent.state_store.get("default")
        history = state.get("messages", []) if state else []
        last = ""
        for m in reversed(history):
            if m.get("role") == "assistant":
                last = m.get("content", "")
                break
        yield f"data: {json.dumps({'type': 'done', 'response': last, 'history': history, 'session_id': 'default', 'title': 'ARF Assistant'}, ensure_ascii=False)}\n\n"
    except asyncio.CancelledError:
        # Client disconnected — cancel the agent
        cancel_evt.set()
        logger.info("SSE client disconnected, cancelling agent")
    except Exception as e:
        import traceback
        logger.error(f"SSE chat error: {traceback.format_exc()}")
        yield f"data: {json.dumps({'type': 'error', 'detail': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        _active_cancel_events.pop("default", None)
        # Reset cancel event so next request starts fresh
        _agent._engine.set_cancel_event(None)
        cancel_evt.clear()


@app.post("/api/chat/cancel")
async def cancel_chat():
    """Cancel the in-flight streaming chat for the default session."""
    evt = _active_cancel_events.get("default")
    if evt and not evt.is_set():
        evt.set()
        logger.info("Chat cancelled via API")
        return JSONResponse({"status": "cancelled"})
    return JSONResponse({"status": "no_active_chat"})


@app.post("/api/chat/undo")
async def undo_chat(steps: int = 1):
    """Undo N user-interaction rounds. Restores checkpointed state."""
    if steps < 1:
        return JSONResponse({"error": "steps must be >= 1"}, status_code=400)
    engine = _agent._engine
    available = engine.checkpoint_count()
    if available < steps:
        return JSONResponse({"status": "insufficient_checkpoints", "available": available, "requested": steps})
    restored = engine.undo(steps)
    if restored is None:
        return JSONResponse({"status": "no_checkpoints"})
    # Write restored state back to state store
    await _agent.state_store.put("default", restored)
    msg_count = len(restored.get("messages", []))
    remaining = engine.checkpoint_count()
    logger.info(f"Undo {steps} round(s): restored to {msg_count} messages, {remaining} checkpoints remain")
    return JSONResponse({"status": "undone", "steps": steps, "messages": msg_count, "remaining_checkpoints": remaining})


@app.get("/api/chat/undo/status")
async def undo_status():
    """Return how many undo checkpoints are available."""
    engine = _agent._engine
    return JSONResponse({"available": engine.checkpoint_count(), "max": 3})


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
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}}, ensure_ascii=False)}\n\n"
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

@app.get("/api/traces/sessions")
async def traces_sessions(limit: int = 20):
    """List sessions with trace data (for TraceView dropdown)."""
    trace_dir = Path("./memory/sessions")
    sessions = []
    if trace_dir.exists():
        for p in sorted(trace_dir.glob("*.json"), reverse=True)[:limit]:
            sid = p.stem
            if not sid or sid == ".json":  # skip corrupted files
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": sid,
                    "event_count": len(data) if isinstance(data, list) else 0,
                })
            except Exception:
                pass
    if not sessions:
        sessions.append({"session_id": "default", "event_count": 0})
    return JSONResponse({"sessions": sessions})

@app.get("/api/traces/sessions/{session_id}")
async def traces_session_detail(session_id: str):
    """Get detailed trace events for a specific session."""
    trace_dir = Path("./memory/sessions")
    path = trace_dir / f"{session_id}.json"
    if not path.exists():
        return JSONResponse({"turns": [], "events": []})
    try:
        events = json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse({"turns": _group_turns(events), "events": events})
    except Exception:
        return JSONResponse({"turns": [], "events": []})

def _group_turns(events: list) -> list:
    """Group trace events by turn number."""
    turns = {}
    for e in events:
        t = e.get("turn", 0)
        if t not in turns:
            turns[t] = {"turn": t, "events": []}
        turns[t]["events"].append(e)
    return sorted(turns.values(), key=lambda x: x["turn"])

@app.get("/api/traces/summary")
async def traces_summary():
    """Summary for TraceView stats bar — from actual data."""
    trace_dir = Path("./memory/sessions")
    total_events = 0
    sessions_count = 0
    for p in trace_dir.glob("*.json") if trace_dir.exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                total_events += len(data)
            sessions_count += 1
        except Exception:
            pass
    if sessions_count == 0:
        sessions_count = 1  # default session always exists
    return JSONResponse({
        "total_sessions": sessions_count,
        "total_events": total_events,
        "total_tokens": total_events,  # rough estimate (each event ≈ 1 token metadata)
        "total_turns": 0,
        "thumbs_up": 0,
        "thumbs_down": 0,
        "total": sessions_count,
    })


@app.get("/api/trace/stream")
async def trace_stream():
    async def gen():
        try:
            async for event in _agent.event_bus.subscribe():
                yield (
                    f"data: {json.dumps({'type': event.type, 'data': event.data, 'turn': event.turn}, ensure_ascii=False)}\n\n"
                )
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/config/register-deepseek")
async def config_register_deepseek(req: dict):
    """Accept DeepSeek API key, persist to .env and in-process env."""
    api_key = (req or {}).get("api_key", "").strip()
    if not api_key:
        return JSONResponse({"error": "API key required"}, status_code=400)

    global _agent

    # Persist to .env file for restart survival
    _save_api_key(api_key)
    # Set for current process
    os.environ["DEEPSEEK_API_KEY"] = api_key
    # Invalidate config cache so next status check re-verifies with new key
    _api_key_cache["checked_at"] = 0

    # Recreate agent so ModelAdapter picks up the new key from os.environ
    from lazy_persistence import save_archive_async, load_archive
    if _agent:
        await save_archive_async(_agent)
    archive = load_archive()
    cfg = AgentConfig.from_yaml("agent.yaml")
    _agent = create_agent(config=cfg)
    set_agent(_agent)
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
    # Re-attach FileTraceStore to new agent's event bus
    from arf.observability import FileTraceStore
    FileTraceStore(_agent.event_bus, dir="./memory/sessions")

    return JSONResponse({
        "ok": True,
        "action": "register_deepseek",
        "models_created": [m.name for m in _agent.config.models],
        "models": [{"name": m.name, "model": m.model} for m in _agent.config.models],
    })


def _save_api_key(key: str) -> None:
    """Write or update DEEPSEEK_API_KEY in .env file."""
    env_path = Path(".env")
    lines: list[str] = []
    found = False
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("DEEPSEEK_API_KEY="):
            lines[i] = f"DEEPSEEK_API_KEY={key}"
            found = True
            break
    if not found:
        lines.append(f"DEEPSEEK_API_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

@app.get("/api/config/status")
async def config_status():
    cfg = _agent.config
    m = cfg.models[0] if cfg.models else None
    configured = await _verify_api_key(cfg)
    return JSONResponse({
        "configured": configured,
        "model_name": m.model if m else "",
        "model_type": "deep_thinking",
        "config_name": m.name if m else "",
        "agent_name": cfg.name,
        "models": [x.name for x in cfg.models],
        "tool_count": len(cfg.tools),
    })


_api_key_cache: dict[str, bool | float] = {"valid": False, "checked_at": 0}

async def _verify_api_key(cfg) -> bool:
    """Quick API call (max_tokens=1) to verify the key. Cached for 60s."""
    now = time.time()
    if now - _api_key_cache["checked_at"] < 60:
        return _api_key_cache["valid"]
    if not cfg or not cfg.models:
        _api_key_cache.update(valid=False, checked_at=now)
        return False
    m = cfg.models[0]
    key = os.environ.get(m.api_key_env, "")
    if not key.strip():
        _api_key_cache.update(valid=False, checked_at=now)
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{m.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": m.model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                },
            )
        valid = resp.status_code == 200
        _api_key_cache.update(valid=valid, checked_at=now)
        return valid
    except Exception:
        _api_key_cache.update(valid=False, checked_at=now)
        return False


@app.get("/api/resources")
async def resources_all():
    """List all resources — matches old frontend ResourcePanel format."""
    system_tools = [{"name": t.name, "description": t.description, "source": "system",
                      "active": t.activation == "kernel", "activation": t.activation}
                    for t in _agent.config.tools]
    system_skills = [{"name": s.name, "description": s.description, "source": "system",
                       "active": s.activation == "kernel", "activation": s.activation}
                     for s in _agent.config.skills]
    # Match old ResourceRegistry.list_all() format
    tools = [{"name": t.name, "description": t.description, "source": "system",
              "readonly": True, "configured": True, "required": False,
              "depends_on": [], "activation": t.activation}
             for t in _agent.config.tools]
    skills = [{"name": s.name, "description": s.description, "source": "system",
               "readonly": True, "configured": True, "required": False,
               "depends_on": [], "activation": s.activation}
              for s in _agent.config.skills]
    models = [{"name": m.name, "description": m.model, "source": "system",
               "readonly": False, "configured": True, "required": True,
               "depends_on": [], "model_name": m.model,
               "config_page": "DeepSeekConfigForm"}
              for m in _agent.config.models]
    return JSONResponse({"tools": tools, "skills": skills, "models": models})

@app.get("/api/resources/unconfigured")
async def resources_unconfigured(required_only: bool = False):
    """List unconfigured resources (stub — all tools are pre-configured)."""
    return JSONResponse([])

@app.get("/api/resources/models/{name}")
async def get_model_config(name: str):
    """Return config for a specific model (used by DeepSeekConfigForm)."""
    for m in _agent.config.models:
        if m.name == name:
            return JSONResponse({"config": {
                "model_name": m.model,
                "base_url": m.api_base,
                "api_key": os.environ.get(m.api_key_env, ""),
            }})
    return JSONResponse({"error": "not found"}, status_code=404)

@app.post("/api/resources/model/{name}/configure")
async def configure_model(name: str, req: dict):
    """Save model config (placeholder — models are pre-configured in agent.yaml)."""
    return JSONResponse({"ok": True})

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
    set_agent(_agent)
    return JSONResponse({"status": "reloaded", "name": cfg.name})


class FeedbackReq(BaseModel):
    rating: int = 0
    comment: str = ""


@app.post("/api/feedback")
async def feedback(req: FeedbackReq):
    log = Path("memory/feedback.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(json.dumps({"timestamp": time.time(), **req.model_dump()}, ensure_ascii=False) + "\n")
    return JSONResponse({"status": "recorded"})

@app.get("/api/feedback/{session_id}")
async def feedback_get(session_id: str):
    """Get feedback for a session (stub)."""
    return JSONResponse([])


@app.get("/api/preferences")
async def preferences():
    """User preferences stub."""
    return JSONResponse({"language": "zh-CN", "theme": "auto"})

@app.get("/api/usage/summary")
async def usage_summary(period: str = "month"):
    """Usage stats from framework UsageTracker."""
    return JSONResponse({
        **_agent.usage_tracker.summary(),
        "sessions": 1,
        "period": period,
    })

@app.get("/api/usage/detail")
async def usage_detail(from_date: str, to_date: str, model: str = ""):
    """Usage detail stub — aggregated data, no per-day breakdown yet."""
    return JSONResponse([])

@app.get("/api/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "agent": _agent.config.name if _agent else "not initialized",
    })

@app.get("/api/debug/state")
async def debug_state():
    state = await _agent.state_store.get("default")
    return JSONResponse({"has_state": state is not None, "messages": len(state.get("messages",[])) if state else 0})

# ---- Session stubs (single infinite session — no session management) ----
from datetime import datetime, timezone

def _session_defaults():
    """Shared defaults for the single-session model."""
    archive = Path("memory/archive.json")
    created_at = datetime.fromtimestamp(archive.stat().st_mtime, tz=timezone.utc).isoformat() if archive.exists() else datetime.now(timezone.utc).isoformat()
    return {"id": "default", "session_id": "default", "title": "ARF Assistant", "created_at": created_at}

@app.get("/trace-viewer")
async def trace_viewer():
    """Serve the standalone trace viewer HTML (framework default debugging tool)."""
    viewer_path = Path(__file__).parent.parent.parent / "arf/observability/trace_viewer.html"
    return FileResponse(viewer_path, media_type="text/html")


@app.get("/api/sessions")
async def sessions_list():
    return JSONResponse([])

@app.post("/api/sessions")
async def sessions_create():
    state = await _agent.state_store.get("default")
    messages = state.get("messages", []) if state else []
    return JSONResponse({**_session_defaults(), "message_count": len(messages)})

@app.get("/api/sessions/active")
async def sessions_active():
    state = await _agent.state_store.get("default")
    messages = state.get("messages", []) if state else []
    return JSONResponse({**_session_defaults(), "message_count": len(messages)})

@app.get("/api/sessions/active/messages")
async def sessions_active_messages():
    state = await _agent.state_store.get("default")
    messages = state.get("messages", []) if state else []
    return JSONResponse(messages)

# ---- WebSocket stub (single session — no real-time sync needed) ----
from fastapi import WebSocket
@app.websocket("/ws")
async def ws_stub(ws: WebSocket):
    await ws.accept()
    await ws.close()

# ---- Resource stats stubs ----
@app.get("/api/traces/resource-stats")
async def traces_resource_stats(period: str = "all"):
    return JSONResponse({"resources": [], "period": period})

@app.get("/api/traces/resource-stats/{name}")
async def traces_resource_stats_detail(name: str, from_date: str = "", to_date: str = ""):
    return JSONResponse({"name": name, "daily": [], "from_date": from_date, "to_date": to_date})

# ---- Trace export stub ----
@app.get("/api/traces/export")
async def traces_export(session_id: str):
    p = Path(f"memory/sessions/{session_id}.json")
    if p.exists():
        return FileResponse(p, media_type="application/json")
    return JSONResponse({"error": "not found"}, status_code=404)

# ---- File upload stub ----
@app.post("/api/upload")
async def upload_file(file: UploadFile = None):
    return JSONResponse({"ok": True, "path": "", "filename": "", "size": 0, "content_type": "", "preview": ""})

# ---- Config test stub ----
@app.post("/api/config/test")
async def config_test(req: dict):
    return JSONResponse({"ok": True, "response": "Connection OK"})

# ---- Usage models pricing stub ----
@app.get("/api/usage/models/pricing")
async def usage_models_pricing():
    return JSONResponse([])

# Static files (frontend) + SPA fallback
frontend_dir = Path("../web/dist")
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets")

    from fastapi.responses import FileResponse
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """SPA fallback: all non-/api/ requests → index.html (Vue Router handles the rest)."""
        return FileResponse(frontend_dir / "index.html")


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
