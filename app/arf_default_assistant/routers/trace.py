"""Trace routes — /api/trace, /api/traces/*, /trace-viewer."""
import json
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, FileResponse

from agent_main import app_context
from routers import state

logger = logging.getLogger("arf-assistant")
router = APIRouter()


def _group_turns(events: list) -> list:
    turns = {}
    for e in events:
        t = e.get("turn", 0)
        if t not in turns:
            turns[t] = {"turn": t, "events": []}
        turns[t]["events"].append(e)
    return sorted(turns.values(), key=lambda x: x["turn"])


@router.get("/api/trace")
async def get_trace():
    events = []
    if app_context.trace_dir.exists():
        for p in sorted(app_context.trace_dir.glob("*.json")):
            try:
                events.extend(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
    return JSONResponse({"events": events})


@router.get("/api/traces/sessions")
async def traces_sessions(limit: int = 20):
    sessions = []
    if app_context.trace_dir.exists():
        for p in sorted(app_context.trace_dir.glob("*.json"), reverse=True)[:limit]:
            sid = p.stem
            if not sid or sid == ".json":
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


@router.get("/api/traces/sessions/{session_id}")
async def traces_session_detail(session_id: str):
    path = app_context.trace_dir / f"{session_id}.json"
    if not path.exists():
        return JSONResponse({"turns": [], "events": []})
    try:
        events = json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse({"turns": _group_turns(events), "events": events})
    except Exception:
        return JSONResponse({"turns": [], "events": []})


@router.get("/api/traces/summary")
async def traces_summary():
    total_events = 0
    sessions_count = 0
    total_tokens = 0
    for p in app_context.trace_dir.glob("*.json") if app_context.trace_dir.exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                total_events += len(data)
                for e in data:
                    usage = e.get("data", {}).get("usage", {})
                    if isinstance(usage, dict):
                        total_tokens += usage.get("total_tokens", 0)
            sessions_count += 1
        except Exception:
            pass
    if sessions_count == 0:
        sessions_count = 1
    return JSONResponse({
        "total_sessions": sessions_count,
        "total_events": total_events,
        "total_tokens": total_tokens,
        "total_turns": 0,
        "sessions": sessions_count,
        "thumbs_up": 0,
        "thumbs_down": 0,
    })


@router.get("/api/trace/stream")
async def trace_stream():
    from fastapi.responses import StreamingResponse

    async def gen():
        try:
            async for event in state._agent.event_bus.subscribe():
                yield (
                    f"data: {json.dumps({'type': event.type, 'data': event.data, 'turn': event.turn}, ensure_ascii=False)}\n\n"
                )
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/trace-viewer")
async def trace_viewer():
    import arf.observability
    viewer_path = Path(arf.observability.__file__).parent / "trace_viewer.html"
    return FileResponse(viewer_path, media_type="text/html")
