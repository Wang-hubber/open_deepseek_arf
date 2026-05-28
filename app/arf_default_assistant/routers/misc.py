"""Misc routes — health, debug, feedback, sessions, usage, resource-stats, etc."""
import json
import logging
import time

from fastapi import APIRouter, UploadFile, WebSocket
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from agent_main import app_context
from routers import state

logger = logging.getLogger("arf-assistant")
router = APIRouter()


class FeedbackReq(BaseModel):
    rating: int = 0
    comment: str = ""


@router.get("/api/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "agent": state._agent.config.name if state._agent else "not initialized",
    })


@router.get("/api/debug/state")
async def debug_state():
    state_data = await state._agent.state_store.get("default")
    return JSONResponse({"has_state": state_data is not None, "messages": len(state_data.get("messages", [])) if state_data else 0})


@router.post("/api/feedback")
async def feedback(req: FeedbackReq):
    log = app_context.workspace_dir / "feedback.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(json.dumps({"timestamp": time.time(), **req.model_dump()}, ensure_ascii=False) + "\n")
    return JSONResponse({"status": "recorded"})


@router.get("/api/feedback/{session_id}")
async def feedback_get(session_id: str):
    return JSONResponse([])


@router.get("/api/preferences")
async def preferences():
    return JSONResponse({"language": "zh-CN", "theme": "auto"})


@router.get("/api/usage/summary")
async def usage_summary(period: str = "month"):
    return JSONResponse({
        **state._agent.usage_tracker.summary(),
        "sessions": 1,
        "period": period,
    })


@router.get("/api/usage/detail")
async def usage_detail(from_date: str, to_date: str, model: str = ""):
    return JSONResponse([])


@router.get("/api/usage/models/pricing")
async def usage_models_pricing():
    return JSONResponse([])


# ---- Session stubs ----
from datetime import datetime, timezone


def _session_defaults():
    state_file = app_context.state_dir / "default.json"
    created_at = datetime.fromtimestamp(state_file.stat().st_mtime, tz=timezone.utc).isoformat() if state_file.exists() else datetime.now(timezone.utc).isoformat()
    return {"id": "default", "session_id": "default", "created_at": created_at}


@router.get("/api/sessions")
async def sessions_list():
    return JSONResponse([])


@router.post("/api/sessions")
async def sessions_create():
    state_data = await state._agent.state_store.get("default")
    messages = state_data.get("messages", []) if state_data else []
    return JSONResponse({**_session_defaults(), "message_count": len(messages)})


@router.get("/api/sessions/active")
async def sessions_active():
    state_data = await state._agent.state_store.get("default")
    messages = state_data.get("messages", []) if state_data else []
    return JSONResponse({**_session_defaults(), "message_count": len(messages)})


@router.get("/api/sessions/active/messages")
async def sessions_active_messages():
    state_data = await state._agent.state_store.get("default")
    messages = state_data.get("messages", []) if state_data else []
    return JSONResponse(messages)


@router.websocket("/ws")
async def ws_stub(ws: WebSocket):
    await ws.accept()
    await ws.close()


@router.post("/api/upload")
async def upload_file(file: UploadFile = None):
    return JSONResponse({"ok": True, "path": "", "filename": "", "size": 0, "content_type": "", "preview": ""})


# ---- Resource stats (from trace data) ----
@router.get("/api/traces/resource-stats")
async def traces_resource_stats(period: str = "all"):
    resources: dict[str, dict] = {}
    for p in app_context.trace_dir.glob("*.json") if app_context.trace_dir.exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue
            for e in data:
                d = e.get("data", {})
                if e.get("type") == "tool_call_end":
                    tn = d.get("tool_name", "unknown")
                    if tn not in resources:
                        resources[tn] = {"call_count": 0, "success_count": 0, "failure_count": 0, "total_duration_ms": 0, "type": "tool", "tokens": 0}
                    r = resources[tn]
                    r["call_count"] += 1
                    if d.get("success"):
                        r["success_count"] += 1
                    else:
                        r["failure_count"] += 1
                    r["total_duration_ms"] += d.get("duration_ms", 0)
                if e.get("type") == "model_call_end":
                    mn = d.get("model", "unknown")
                    if mn not in resources:
                        resources[mn] = {"call_count": 0, "success_count": 0, "failure_count": 0, "total_duration_ms": 0, "type": "model", "tokens": 0}
                    r = resources[mn]
                    r["call_count"] += 1
                    r["success_count"] += 1
                    r["tokens"] += d.get("usage", {}).get("total_tokens", 0)
        except Exception:
            pass
    result = []
    for name, r in sorted(resources.items(), key=lambda x: -x[1]["call_count"]):
        avg_ms = round(r["total_duration_ms"] / r["call_count"], 1) if r["call_count"] > 0 else 0
        result.append({
            "name": name,
            "type": r["type"],
            "call_count": r["call_count"],
            "success_count": r["success_count"],
            "failure_count": r["failure_count"],
            "avg_duration_ms": avg_ms,
            "tokens": r["tokens"],
        })
    return JSONResponse({"resources": result, "period": period})


@router.get("/api/traces/resource-stats/{name}")
async def traces_resource_stats_detail(name: str, from_date: str = "", to_date: str = ""):
    daily: dict[str, dict] = {}
    for p in app_context.trace_dir.glob("*.json") if app_context.trace_dir.exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue
            for e in data:
                d = e.get("data", {})
                if d.get("tool_name") == name or d.get("model") == name:
                    ts = e.get("timestamp", 0)
                    day = __import__("datetime").datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    if day not in daily:
                        daily[day] = {"call_count": 0, "success_count": 0, "failure_count": 0, "total_duration_ms": 0}
                    r = daily[day]
                    r["call_count"] += 1
                    if e.get("type") == "tool_call_end":
                        if d.get("success"):
                            r["success_count"] += 1
                        else:
                            r["failure_count"] += 1
                        r["total_duration_ms"] += d.get("duration_ms", 0)
                    elif e.get("type") == "model_call_end":
                        r["success_count"] += 1
        except Exception:
            pass
    return JSONResponse({
        "name": name,
        "daily": [{
            "day": k,
            "call_count": v["call_count"],
            "success_count": v["success_count"],
            "failure_count": v["failure_count"],
            "avg_duration_ms": round(v["total_duration_ms"] / v["call_count"], 1) if v["call_count"] > 0 else None,
        } for k, v in sorted(daily.items())],
        "from_date": from_date, "to_date": to_date,
    })


@router.get("/api/traces/export")
async def traces_export(session_id: str):
    p = app_context.trace_dir / f"{session_id}.json"
    if p.exists():
        return FileResponse(p, media_type="application/json")
    return JSONResponse({"error": "not found"}, status_code=404)
