"""API routes for config, resources, chat, sessions, and traces."""

import json
import os
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..resources.model_adapter import ModelAdapter
from .database import (
    init_db, record_usage, insert_trace_events,
)
from .session_manager import SessionManager
from .sessions import list_archives, get_archive, archive_session, update_title, delete_archive, DEFAULT_TITLE

router = APIRouter(prefix="/api")

_mgr: SessionManager | None = None


def set_manager(mgr: SessionManager) -> None:
    global _mgr
    _mgr = mgr


def get_mgr() -> SessionManager:
    if _mgr is None:
        raise RuntimeError("SessionManager not initialized")
    return _mgr


# ---- pydantic models --------------------------------------------------


class ModelConfig(BaseModel):
    model_config = {"extra": "allow"}
    base_url: str
    api_key: str
    model_name: str
    temperature: float = 0.7
    max_tokens: int = 4096


class DeepSeekRegisterRequest(BaseModel):
    api_key: str


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    stream: bool = True
    new_session: bool = False


class UpdateTitleRequest(BaseModel):
    title: str


class PricingUpdate(BaseModel):
    input_price: float = 0
    output_price: float = 0
    currency: str = "CNY"


class FeedbackRequest(BaseModel):
    session_id: str
    message_index: int
    rating: int
    feedback_text: str = ""


class HookDef(BaseModel):
    event: str
    name: str
    command: str
    timeout: int = 30
    enabled: bool = True
    matcher: str | None = None


# ---- config routes ----------------------------------------------------


@router.get("/config/status")
def config_status(mgr: SessionManager = Depends(get_mgr)):
    agent_yaml = mgr.read_agent_yaml()
    preferred = (agent_yaml.get("agent") or {}).get("model")
    registry = mgr.get_registry()
    pending = registry.list_unconfigured(required_only=True)
    if pending:
        return {
            "configured": False,
            "model_name": "",
            "model_type": "deep_thinking",
            "config_name": "",
            "pending_required": pending,
        }
    resolved = mgr.resolve_model_config(preferred)
    if resolved:
        name, cfg = resolved
        models = registry._items.get("models", {}).get(name, {})
        return {
            "configured": True,
            "model_name": cfg.get("model_name", ""),
            "model_type": models.get("model_type", "deep_thinking"),
            "config_name": name,
        }
    return {"configured": False, "model_name": "", "model_type": "deep_thinking", "config_name": ""}


@router.post("/config/test")
def config_test(payload: ModelConfig, mgr: SessionManager = Depends(get_mgr)):
    adapter = ModelAdapter(payload.model_dump())
    try:
        response = adapter.chat(messages=[{"role": "user", "content": "hello"}])
        return {"ok": True, "response": response}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/config/save")
def config_save(payload: ModelConfig, mgr: SessionManager = Depends(get_mgr)):
    config_name = _validate_resource_name(
        getattr(payload, "config_name", None) or "deep_thinking"
    )
    path = mgr.workspace_dir / "models" / config_name / "config.yaml"
    config = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    config["name"] = config_name
    config["config"] = payload.model_dump()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
    mgr.reset_resource_state()
    return {"ok": True}


DEEPSEEK_BASE_URL = os.environ.get("ARF_DEEPSEEK_BASE_URL", "https://api.deepseek.com")

_DEFAULT_DS_COMMON = {
    "top_p": 1.0,
    "response_format": "text",
    "stream": True,
}

DEEPSEEK_MODEL_SPECS = {
    "deep_thinking": {
        "model_name": "deepseek-v4-pro",
        "temperature": 0.7,
        "max_tokens": 100000,
        "thinking_enabled": True,
        "reasoning_effort": "max",
        **_DEFAULT_DS_COMMON,
    },
    "quick_thinking": {
        "model_name": "deepseek-v4-flash",
        "temperature": 0.3,
        "max_tokens": 50000,
        "thinking_enabled": True,
        "reasoning_effort": "high",
        **_DEFAULT_DS_COMMON,
    },
    "quick_no_thinking": {
        "model_name": "deepseek-v4-flash",
        "temperature": 0.3,
        "max_tokens": 102400,
        "thinking_enabled": False,
        **_DEFAULT_DS_COMMON,
    },
}


@router.post("/config/register-deepseek")
def config_register_deepseek(payload: DeepSeekRegisterRequest, mgr: SessionManager = Depends(get_mgr)):
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required")

    created = []
    for name, spec in DEEPSEEK_MODEL_SPECS.items():
        model_dir = mgr.workspace_dir / "models" / name
        model_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "name": name,
            "model_type": name,
            "config": {
                "base_url": DEEPSEEK_BASE_URL,
                "api_key": api_key,
                "model_name": spec["model_name"],
                "temperature": spec["temperature"],
                "max_tokens": spec["max_tokens"],
                "thinking_enabled": spec.get("thinking_enabled", False),
            },
        }
        if "reasoning_effort" in spec:
            config["config"]["reasoning_effort"] = spec["reasoning_effort"]

        config_path = model_dir / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
        created.append({"name": name, "model_name": spec["model_name"]})

    agent_yaml_path = mgr.workspace_dir / "arf_agent.yaml"
    agent_cfg = {}
    if agent_yaml_path.exists():
        agent_cfg = yaml.safe_load(agent_yaml_path.read_text(encoding="utf-8")) or {}
    agent_cfg.setdefault("agent", {})["model"] = "quick_no_thinking"
    agent_yaml_path.write_text(
        yaml.safe_dump(agent_cfg, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )

    mgr.reset_resource_state()
    return {"ok": True, "models": created, "active_model": "quick_no_thinking"}


# ---- trace routes -------------------------------------------------------


@router.get("/traces/sessions")
def trace_sessions(limit: int = 20):
    from .database import get_trace_session_list
    sessions = get_trace_session_list("admin", limit)
    for s in sessions:
        try:
            sid = s["session_id"]
            row = _get_session_row(sid)
            if row:
                s["title"] = row.get("title", "")
        except Exception:
            s["title"] = ""
    return {"sessions": sessions}


def _get_session_row(session_id: str) -> dict | None:
    try:
        from .database import _get_conn
        cur = _get_conn().execute(
            "SELECT title FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None


@router.get("/traces/sessions/{session_id}")
def trace_session_detail(session_id: str):
    from .database import get_trace_session_detail
    events = get_trace_session_detail(session_id, "admin")
    if not events:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "events": events}


@router.get("/traces/summary")
def trace_summary():
    from .database import get_trace_summary, get_feedback_summary
    stats = get_trace_summary("admin")
    feedback = get_feedback_summary("admin")
    stats.update(feedback)
    return stats


@router.get("/traces/export")
def trace_export(session_id: str):
    from .database import get_trace_session_detail
    events = get_trace_session_detail(session_id, "admin")
    if not events:
        raise HTTPException(status_code=404, detail="Session not found")
    from datetime import datetime, timezone
    return {"session_id": session_id, "events": events, "exported_at": datetime.now(timezone.utc).isoformat()}


# ---- feedback routes ----------------------------------------------------


@router.post("/feedback")
def submit_feedback(payload: FeedbackRequest):
    if payload.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="Rating must be 1 or -1")
    from .database import insert_feedback
    insert_feedback(payload.session_id, payload.message_index, payload.rating, payload.feedback_text)
    return {"ok": True}


@router.get("/feedback/{session_id}")
def get_feedback(session_id: str):
    from .database import get_feedback_for_session
    return {"feedback": get_feedback_for_session(session_id)}


# ---- resource stats routes ---------------------------------------------


@router.get("/traces/resource-stats")
def trace_resource_stats(period: str = "all"):
    from .database import get_resource_stats
    return {"resources": get_resource_stats("admin", period)}


@router.get("/traces/resource-stats/export")
def trace_resource_stats_export(period: str = "all"):
    from .database import get_resource_stats
    import csv, io
    from fastapi.responses import Response

    stats = get_resource_stats("admin", period)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Resource", "Call Count", "Success", "Failure", "Avg Duration (ms)"])
    for r in stats:
        writer.writerow(
            [r["name"], r["call_count"], r["success_count"], r["failure_count"], r["avg_duration_ms"]]
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=resource-stats-{period}.csv"},
    )


@router.get("/traces/resource-stats/{resource_name}")
def trace_resource_detail(resource_name: str, from_date: str = "", to_date: str = ""):
    from .database import get_resource_detail
    return {
        "resource_name": resource_name,
        "daily": get_resource_detail("admin", resource_name, from_date, to_date),
    }


@router.get("/traces/resource-stats/{resource_name}/export")
def trace_resource_detail_export(resource_name: str, from_date: str = "", to_date: str = ""):
    from .database import get_resource_detail
    import csv, io
    from fastapi.responses import Response

    daily = get_resource_detail("admin", resource_name, from_date, to_date)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Calls", "Success", "Failure", "Avg Duration (ms)"])
    for d in daily:
        writer.writerow(
            [d["day"], d["call_count"], d["success_count"], d["failure_count"],
             round(d["avg_duration_ms"] or 0, 1)]
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=resource-{resource_name}-detail.csv"},
    )


# ---- resource routes --------------------------------------------------


@router.get("/resources")
def list_resources(mgr: SessionManager = Depends(get_mgr)):
    return mgr.get_registry().list_all()


@router.get("/resources/unconfigured")
def list_unconfigured(required_only: bool = False, mgr: SessionManager = Depends(get_mgr)):
    return mgr.get_registry().list_unconfigured(required_only=required_only)


@router.get("/resources/{resource_type}/{name}")
def get_resource(resource_type: str, name: str, mgr: SessionManager = Depends(get_mgr)):
    if resource_type not in ("models", "tools", "skills"):
        raise HTTPException(status_code=400, detail=f"Invalid resource type: {resource_type}")
    item = mgr.get_registry().get(resource_type, name)
    if item is None:
        raise HTTPException(status_code=404, detail=f"{resource_type}/{name} not found")
    return item


def _validate_resource_name(name: str) -> str:
    if not name or ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail=f"Invalid resource name: {name}")
    return name


@router.post("/resources/{resource_type}/{name}/configure")
def configure_resource(resource_type: str, name: str, payload: dict, mgr: SessionManager = Depends(get_mgr)):
    if resource_type not in ("model", "tool", "skill"):
        raise HTTPException(status_code=400, detail=f"Invalid resource type: {resource_type}")
    name = _validate_resource_name(name)
    rtype = resource_type + "s"
    item = mgr.get_registry().get(rtype, name)
    if item is None:
        raise HTTPException(status_code=404, detail=f"{resource_type}/{name} not found")
    ws_dir = mgr.workspace_dir
    target_dir = (ws_dir / rtype / name).resolve()
    if not str(target_dir).startswith(str(ws_dir.resolve()) + os.sep) and str(target_dir) != str(ws_dir.resolve()):
        raise HTTPException(status_code=400, detail="Resource path escapes workspace")
    target_dir.mkdir(parents=True, exist_ok=True)
    if resource_type == "model":
        config_file = target_dir / "config.yaml"
        config = {
            "name": name,
            "model_type": item.get("model_type", "deep_thinking"),
            "description": item.get("description", ""),
            "config": payload.get("config", payload),
        }
        # Preserve metadata from system config_default so reload doesn't lose it
        for key in ("config_page", "config_template"):
            if item.get(key):
                config[key] = item[key]
        for key in ("depends_on", "required"):
            if key in item:
                config[key] = item[key]
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
    else:
        raise HTTPException(status_code=400, detail=f"Configure is not supported for {resource_type} yet")
    mgr.reset_resource_state()
    return {"ok": True}


@router.get("/resources/{resource_type}/{name}/deps")
def check_resource_deps(resource_type: str, name: str, mgr: SessionManager = Depends(get_mgr)):
    if resource_type not in ("models", "tools", "skills"):
        raise HTTPException(status_code=400, detail=f"Invalid resource type: {resource_type}")
    return mgr.get_registry().check_deps(resource_type, name)


# ---- project info -----------------------------------------------------


@router.get("/project/current")
def current_project(mgr: SessionManager = Depends(get_mgr)):
    return {
        "workspace": str(mgr.workspace_dir),
        "system_resources": str(mgr.system_dir),
    }


# ---- chat route -------------------------------------------------------


@router.post("/chat")
def chat(payload: ChatRequest, mgr: SessionManager = Depends(get_mgr)):
    agent = mgr.get_agent()
    agent.language = "zh"
    workspace_dir = str(mgr.workspace_dir)

    if payload.new_session:
        old_history = list(mgr.session_history)
        old_start = mgr.session_start_time
        old_title = mgr.session_title
        if old_history and len(old_history) >= 2:
            try:
                sid = archive_session(old_history, old_start, workspace_dir, old_title, graph_traces=mgr.last_traces if hasattr(mgr, 'last_traces') else None, usage=mgr.last_usage if hasattr(mgr, 'last_usage') else None)
                if sid:
                    from .database import insert_session, update_session
                    fpath = f"memory/sessions/{sid}.json"
                    insert_session(sid, "admin", old_title, fpath)
                    fp = Path(workspace_dir) / fpath
                    if fp.exists():
                        sz = fp.stat().st_size / (1024 * 1024)
                        turns = len(old_history) // 2
                        update_session(sid, turn_count=turns, json_size_mb=round(sz, 3), message_count=len(old_history))
            except Exception:
                pass
        mgr.reset_session_history()
        mgr.session_title = _placeholder_title(mgr.session_start_time, workspace_dir)

        try:
            from .database import insert_session
            sid = mgr.session_start_time.strftime("%Y%m%d_%H%M%S")
            insert_session(sid, "admin", mgr.session_title, filepath=None)
        except Exception:
            pass

    if payload.stream:
        return StreamingResponse(
            _stream_chat(agent, payload, mgr, workspace_dir),
            media_type="text/event-stream",
        )

    try:
        response, full_messages, _, usage, traces = agent.chat_with_tools(
            payload.message, payload.history, workspace_dir
        )
        mgr.track_session(payload.message, response)
        if traces:
            mgr.last_traces = traces
            sid = mgr.current_session_id
            enrich = lambda t: {**t, "session_id": sid, "username": "admin"}
            try:
                insert_trace_events([enrich(t) for t in traces])
            except Exception:
                pass
        if usage:
            mgr.last_usage = usage
            if usage.get("total_tokens"):
                record_usage(
                    username="admin",
                    model_name=agent.model.model_name,
                    model_type="deep_thinking",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )
        display_history = _display_history(full_messages)
        return {"response": response, "history": display_history}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---- upload route ------------------------------------------------------


MAX_UPLOAD_BYTES = 15 * 1024 * 1024


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), mgr: SessionManager = Depends(get_mgr)):
    ws = mgr.workspace_dir
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 15 MB limit")

    uploads_dir = ws / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    filename = file.filename or "untitled"
    dest = uploads_dir / filename
    stem, suffix = (filename.rsplit(".", 1) if "." in filename else (filename, ""))
    suffix = f".{suffix}" if suffix else ""
    counter = 1
    while dest.exists():
        dest = uploads_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    dest.write_bytes(content)

    preview = ""
    text_extensions = {
        ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json",
        ".yaml", ".yml", ".xml", ".csv", ".log", ".sh", ".bash", ".cfg",
        ".ini", ".toml", ".sql", ".rs", ".go", ".java", ".c", ".cpp", ".h",
    }
    if suffix.lower() in text_extensions:
        try:
            text = content.decode("utf-8", errors="replace")
            preview = text[:2000]
            if len(text) > 2000:
                preview += "\n... (truncated)"
        except Exception:
            pass

    return {
        "ok": True,
        "path": str(dest.relative_to(ws)),
        "filename": dest.name,
        "size": len(content),
        "content_type": file.content_type or "application/octet-stream",
        "preview": preview,
    }


# ---- download route ----------------------------------------------------


@router.get("/download")
def download_file(file: str, mgr: SessionManager = Depends(get_mgr)):
    ws = mgr.workspace_dir
    target = (ws / file).resolve()
    if not str(target).startswith(str(ws.resolve())):
        raise HTTPException(status_code=400, detail="Path escapes workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


# ---- session archive routes -------------------------------------------


@router.get("/sessions/active")
def get_active_session(mgr: SessionManager = Depends(get_mgr)):
    if not mgr.session_history:
        return None
    return {
        "id": mgr.session_start_time.strftime("%Y%m%d_%H%M%S"),
        "title": mgr.session_title,
        "created_at": mgr.session_start_time.isoformat(),
        "message_count": len(mgr.session_history),
    }


@router.get("/sessions/active/messages")
def get_active_session_messages(mgr: SessionManager = Depends(get_mgr)):
    return mgr.session_history


@router.get("/sessions")
def list_sessions():
    from .database import list_sessions as db_list
    return db_list("admin")


@router.get("/sessions/{session_id}")
def get_session(session_id: str, mgr: SessionManager = Depends(get_mgr)):
    from .database import get_session as db_get
    row = db_get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.get("filepath"):
        archive = get_archive(session_id, str(mgr.workspace_dir))
        if archive:
            return archive
    return {
        "id": row["session_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "ended_at": row["updated_at"],
        "message_count": row["message_count"],
        "turn_count": row["turn_count"],
        "json_size_mb": row["json_size_mb"],
    }


@router.post("/sessions")
def create_session(mgr: SessionManager = Depends(get_mgr)):
    old_history = list(mgr.session_history)
    old_start = mgr.session_start_time
    old_title = mgr.session_title

    mgr.reset_session_history()
    now = mgr.session_start_time

    if old_history and len(old_history) >= 2:
        try:
            sid = archive_session(old_history, old_start, str(mgr.workspace_dir), old_title, graph_traces=mgr.last_traces if hasattr(mgr, 'last_traces') else None, usage=mgr.last_usage if hasattr(mgr, 'last_usage') else None)
            if sid:
                from .database import insert_session, update_session
                insert_session(sid, "admin", old_title, f"memory/sessions/{sid}.json")
                fpath = Path(str(mgr.workspace_dir)) / "memory" / "sessions" / f"{sid}.json"
                if fpath.exists():
                    sz = fpath.stat().st_size / (1024 * 1024)
                    turns = len(old_history) // 2
                    update_session(sid, turn_count=turns, json_size_mb=round(sz, 3), message_count=len(old_history))
        except Exception:
            pass

    mgr.session_title = _placeholder_title(now, str(mgr.workspace_dir))

    sid = now.strftime("%Y%m%d_%H%M%S")
    try:
        from .database import insert_session
        insert_session(sid, "admin", mgr.session_title, filepath=None)
    except Exception:
        pass

    return {
        "id": sid,
        "title": mgr.session_title,
        "created_at": now.isoformat(),
        "message_count": 0,
        "fast_model_configured": mgr.is_fast_model_configured(),
    }


@router.put("/sessions/{session_id}")
def update_session_title(session_id: str, payload: UpdateTitleRequest, mgr: SessionManager = Depends(get_mgr)):
    ok = update_title(session_id, payload.title.strip(), str(mgr.workspace_dir))
    if not ok:
        from .database import update_session
        update_session(session_id, title=payload.title.strip())
        return {"ok": True}
    from .database import update_session
    update_session(session_id, title=payload.title.strip())
    return {"ok": True}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, mgr: SessionManager = Depends(get_mgr)):
    ok = delete_archive(session_id, str(mgr.workspace_dir))
    from .database import delete_session_db
    delete_session_db(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@router.post("/sessions/active/title")
def generate_active_title(mgr: SessionManager = Depends(get_mgr)):
    if not mgr.session_history:
        return {"title": mgr.session_title}
    mgr.needs_title = False
    try:
        title = _generate_title(list(mgr.session_history), mgr)
        if title:
            mgr.session_title = title
            try:
                from .database import update_session
                sid = mgr.session_start_time.strftime("%Y%m%d_%H%M%S")
                update_session(sid, title=title)
            except Exception:
                pass
    except Exception:
        pass
    return {"title": mgr.session_title}


# ---- hook management routes -------------------------------------------


@router.get("/hooks")
def list_hooks(mgr: SessionManager = Depends(get_mgr)):
    return mgr.get_hook_runner().list_hooks()


@router.post("/hooks")
def add_hook(payload: HookDef, mgr: SessionManager = Depends(get_mgr)):
    ok = mgr.get_hook_runner().add_hook(payload.event, payload.model_dump())
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid event: {payload.event}")
    return {"ok": True}


@router.put("/hooks/{name}")
def update_hook(name: str, payload: dict, mgr: SessionManager = Depends(get_mgr)):
    event = payload.pop("event", "")
    if not event:
        raise HTTPException(status_code=400, detail="event is required")
    ok = mgr.get_hook_runner().update_hook(event, name, payload)
    if not ok:
        raise HTTPException(status_code=404, detail="Hook not found")
    return {"ok": True}


@router.delete("/hooks/{name}")
def delete_hook(name: str, event: str = "SessionStart", mgr: SessionManager = Depends(get_mgr)):
    ok = mgr.get_hook_runner().remove_hook(event, name)
    if not ok:
        raise HTTPException(status_code=404, detail="Hook not found")
    return {"ok": True}


# ---- usage routes ------------------------------------------------------


@router.get("/usage/summary")
def usage_summary(period: str = "month"):
    from .database import get_usage_summary
    return get_usage_summary("admin", period)


@router.get("/usage/detail")
def usage_detail(from_date: str, to_date: str, model: str = ""):
    from .database import get_usage_detail
    m = model if model else None
    return get_usage_detail("admin", from_date, to_date, m)


@router.get("/usage/models/pricing")
def usage_get_pricing():
    from .database import get_model_pricing
    return get_model_pricing("admin")


@router.put("/usage/models/{name}/pricing")
def usage_update_pricing(name: str, payload: PricingUpdate):
    from .database import set_model_pricing
    set_model_pricing("admin", name, payload.input_price, payload.output_price, payload.currency)
    return {"ok": True}


# ---- SSE helpers -------------------------------------------------------


def _display_history(full_messages: list[dict]) -> list[dict]:
    return [m for m in full_messages if m["role"] in ("user", "assistant") and "tool_calls" not in m]


def _stream_chat(agent, payload: ChatRequest, mgr, project_dir: str):
    reasoning_text = ""
    mgr.session_history.append({"role": "user", "content": payload.message})
    try:
        for event in agent.chat_stream_with_tools(
            payload.message, payload.history, project_dir
        ):
            etype = event.get("type")
            if etype == "chunk":
                if event.get("reasoning"):
                    reasoning_text += event["reasoning"]
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                continue
            if etype == "usage":
                record_usage(
                    username="admin",
                    model_name=agent.model.model_name,
                    model_type="deep_thinking",
                    prompt_tokens=event.get("prompt_tokens", 0),
                    completion_tokens=event.get("completion_tokens", 0),
                )
                if mgr.last_usage is None:
                    mgr.last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    mgr.last_usage[k] = mgr.last_usage.get(k, 0) + event.get(k, 0)
                continue
            if etype == "tool_call":
                mgr.session_history.append({
                    "role": "tool_call",
                    "content": f"[{event.get('name', event.get('tool', ''))}] {event.get('arguments', '')}",
                    "tool_call_id": event.get("id", ""),
                    "name": event.get("name", event.get("tool", "")),
                    "arguments": event.get("arguments", ""),
                })
            elif etype == "tool_result":
                mgr.session_history.append({
                    "role": "tool_result",
                    "content": str(event.get("result", ""))[:500],
                    "tool_call_id": event.get("id", ""),
                    "name": event.get("tool", ""),
                })
            elif etype == "done":
                if reasoning_text or event.get("response"):
                    entry: dict = {"role": "assistant", "content": event.get("response", "")}
                    if reasoning_text:
                        entry["reasoning_content"] = reasoning_text
                    mgr.session_history.append(entry)
                reasoning_text = ""

                traces = event.get("traces", [])
                if traces:
                    mgr.last_traces = traces
                    sid = mgr.current_session_id
                    enrich = lambda t: {**t, "session_id": sid, "username": "admin"}
                    try:
                        insert_trace_events([enrich(t) for t in traces])
                    except Exception:
                        pass
                usage = event.get("usage", {})
                if usage:
                    mgr.last_usage = usage

                if mgr.needs_title:
                    mgr.needs_title = False
                    try:
                        title = _generate_title(list(mgr.session_history), mgr)
                        if title:
                            mgr.session_title = title
                            try:
                                from .database import insert_session
                                sid = mgr.session_start_time.strftime("%Y%m%d_%H%M%S")
                                insert_session(sid, "admin", title, filepath=None)
                            except Exception:
                                pass
                    except Exception:
                        pass
                event["title"] = mgr.session_title
                event["session_id"] = mgr.session_start_time.strftime("%Y%m%d_%H%M%S")
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)}, ensure_ascii=False)}\n\n"


def _placeholder_title(start_time, workspace_dir: str) -> str:
    base = f"新会话 · {start_time.strftime('%H:%M')}"
    existing = list_archives(workspace_dir)
    if any(s["title"].startswith(base) for s in existing):
        return f"新会话 · {start_time.strftime('%H:%M:%S')}"
    return base


def _generate_title(history: list[dict], mgr) -> str | None:
    if not history:
        return None

    lines = []
    total = 0
    for m in history:
        role = m.get("role", "?")
        content = m.get("content", "")
        if not content:
            continue
        if len(content) > 500:
            content = content[:500] + "..."
        line = f"[{role}]: {content}"
        if total + len(line) > 3000:
            break
        lines.append(line)
        total += len(line)
    conv_text = "\n\n".join(lines)

    prompt = (
        "Based on the conversation below, generate a short title "
        "(3-6 words, in the same language as the conversation) "
        "that summarizes what the user was doing or asking about. "
        "Return ONLY the title text, no quotes, no prefixes, no other text.\n\n"
        f"{conv_text}"
    )

    try:
        model = mgr.load_fast_model() or mgr.get_agent().model
    except Exception:
        return None

    try:
        title = model.chat([{"role": "user", "content": prompt}])
        title = title.strip().strip('"').strip("'").strip("。").strip()
        return title[:60] if title else None
    except Exception:
        return None
