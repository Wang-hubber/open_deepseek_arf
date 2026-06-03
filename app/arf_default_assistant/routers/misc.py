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


# ---- Session endpoints ----
import uuid as _uuid


def _default_title(state_data: dict | None) -> str:
    """Derive a title from the first user message, or return empty string."""
    if not state_data:
        return ""
    msgs = state_data.get("messages", [])
    for m in msgs:
        if m.get("role") == "user":
            text = m.get("content", "")
            if text:
                return text[:10] + ("..." if len(text) > 10 else "")
    return ""


def _session_info(state_data: dict | None, session_id: str) -> dict:
    """Build a session info dict from state data."""
    title = ""
    if state_data:
        title = state_data.get("session_title", "") or _default_title(state_data)
        return {
            "id": session_id,
            "session_id": session_id,
            "title": title,
            "message_count": len(state_data.get("messages", [])),
            "active": state_data.get("session_active", False),
        }
    return {"id": session_id, "session_id": session_id,
            "title": "", "message_count": 0, "active": False}


@router.get("/api/sessions")
async def sessions_list():
    sids = await state._agent.state_store.list_sessions()
    sessions = []
    for sid in sids:
        data = await state._agent.state_store.get(sid)
        sessions.append(_session_info(data, sid))
    return JSONResponse(sessions)


@router.post("/api/sessions")
async def sessions_create():
    sid = str(_uuid.uuid4())
    return JSONResponse({"id": sid, "session_id": sid,
                         "title": "", "message_count": 0, "active": False})


@router.get("/api/sessions/active")
async def sessions_active():
    return JSONResponse(list(state._agent._active_sessions))


@router.get("/api/sessions/{session_id}/messages")
async def sessions_messages(session_id: str):
    data = await state._agent.state_store.get(session_id)
    if data is None:
        return JSONResponse({"error": "session not found"}, status_code=404)
    return JSONResponse(data.get("messages", []))


@router.patch("/api/sessions/{session_id}")
async def sessions_update(session_id: str, req: dict):
    """Update session metadata (e.g. title)."""
    data = await state._agent.state_store.get(session_id)
    if data is None:
        return JSONResponse({"error": "session not found"}, status_code=404)
    if "title" in req:
        data["session_title"] = req["title"]
        await state._agent.state_store.put(session_id, data)
    return JSONResponse(_session_info(data, session_id))


@router.delete("/api/sessions/{session_id}")
async def sessions_delete(session_id: str):
    await state._agent.state_store.delete(session_id)
    state._agent._active_sessions.discard(session_id)
    return JSONResponse({"status": "deleted", "session_id": session_id})


@router.websocket("/ws")
async def ws_stub(ws: WebSocket):
    await ws.accept()
    await ws.close()


@router.get("/api/files")
async def list_workspace_files():
    """List all files in workspace (for file tree panel). Max depth 4, skip hidden."""
    from pathlib import Path
    root = app_context.workspace_dir
    entries: list[dict] = []

    def _walk_data_files(dir: Path, depth: int = 0):
        """Walk data/files/ sub-tree (simplified, no depth limit)."""
        if depth > 3:
            return
        for p in sorted(dir.iterdir()):
            if p.name.startswith('.'):
                continue
            if p.is_dir():
                entries.append({"name": p.name, "type": "dir",
                               "path": str(p.relative_to(root)), "children": []})
                _walk_data_files(p, depth + 1)
            else:
                entries.append({"name": p.name, "type": "file",
                               "path": str(p.relative_to(root)),
                               "size": p.stat().st_size,
                               "suffix": p.suffix.lower()})

    def _walk(dir: Path, depth: int = 0):
        if depth > 4:
            return
        for p in sorted(dir.iterdir()):
            if p.name.startswith('.') or p.name.startswith('__pycache__'):
                continue
            if p.name in ('node_modules', 'logs', 'memory'):
                continue
            if p.name == 'data' and depth == 0:
                for sub in sorted(p.iterdir()):
                    if sub.name == 'files' and sub.is_dir():
                        entries.append({"name": "data/files", "type": "dir",
                                       "path": str(sub.relative_to(root)), "children": []})
                        _walk_data_files(sub, depth + 1)
                continue
            if p.is_dir():
                entries.append({"name": p.name, "type": "dir",
                               "path": str(p.relative_to(root)), "children": []})
                _walk(p, depth + 1)
            else:
                entries.append({"name": p.name, "type": "file",
                               "path": str(p.relative_to(root)),
                               "size": p.stat().st_size,
                               "suffix": p.suffix.lower()})
    _walk(root)
    return JSONResponse({"ok": True, "root": str(root), "files": entries})


@router.get("/api/files/{file_path:path}")
async def serve_workspace_file(file_path: str, download: str = ""):
    """Serve a workspace file for preview (md/txt/html) or download (others).

    Preview types render inline in browser; all others force download.
    Add ?download=1 to force download even for preview-able types.
    """
    from pathlib import Path
    root = app_context.workspace_dir
    target = (root / file_path).resolve()

    # Security: must be within workspace
    if not str(target).startswith(str(root.resolve())):
        return JSONResponse({"error": "path escapes workspace"}, status_code=403)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)

    preview_suffixes = {'.md': 'text/markdown', '.txt': 'text/plain',
                       '.html': 'text/html', '.htm': 'text/html',
                       '.json': 'application/json', '.csv': 'text/csv',
                       '.yaml': 'text/plain', '.yml': 'text/plain',
                       '.py': 'text/plain', '.log': 'text/plain'}

    if download != '1' and download != 'true' and target.suffix.lower() in preview_suffixes:
        media_type = preview_suffixes[target.suffix.lower()]
        return FileResponse(target, media_type=media_type,
                           headers={"Content-Disposition": f"inline; filename=\"{target.name}\""})
    return FileResponse(target, filename=target.name,
                       headers={"Content-Disposition": f"attachment; filename=\"{target.name}\""})


@router.post("/api/upload")
async def upload_file(file: UploadFile = None):
    """Upload a file to data/files/uploads/ directory."""
    if not file or not file.filename:
        return JSONResponse({"ok": False, "error": "no file"}, status_code=400)
    from pathlib import Path
    upload_dir = app_context.files_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / file.filename
    content = await file.read()
    target.write_bytes(content)
    preview_suffixes = {'.md', '.txt', '.html', '.htm', '.json', '.csv', '.yaml', '.yml', '.py', '.log'}
    return JSONResponse({"ok": True, "path": str(target.relative_to(app_context.workspace_dir)),
                        "filename": file.filename, "size": len(content),
                        "content_type": file.content_type or "",
                        "preview": target.suffix.lower() in preview_suffixes})


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
