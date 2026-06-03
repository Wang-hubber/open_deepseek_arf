"""Chat routes — /api/chat, cancel, approve, undo."""
import asyncio
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from agent_main import app_context
from routers import state

logger = logging.getLogger("arf-assistant")
router = APIRouter()


class ChatReq(BaseModel):
    message: str
    stream: bool = False
    history: list[dict] | None = None
    new_session: bool = False
    session_id: str = ""


@router.post("/api/chat")
async def chat(req: ChatReq):
    sid = req.session_id or ""
    if req.stream:
        return StreamingResponse(
            _ndjson_stream(req.message, sid),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        result = await state._agent.chat(req.message, session_id=sid)
        return JSONResponse({"content": result, "session_id": sid or "default"})
    except Exception as e:
        return JSONResponse({"content": "", "error": str(e)}, status_code=500)


async def _ndjson_stream(message: str, session_id: str = ""):
    """Stream agent events as NDJSON (application/x-ndjson).
    Each line is a complete JSON object terminated by \\n.
    Cancellation via asyncio.CancelledError propagation."""
    try:
        async for event in state._agent.astream(message, session_id=session_id):
            line = json.dumps({
                "type": event.type,
                **event.data,
            }, ensure_ascii=False, default=str) + "\n"
            yield line.encode("utf-8")
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        logger.info("Stream client disconnected, cancelling")
    except Exception as e:
        import traceback
        logger.error(f"Stream error: {traceback.format_exc()}")
        yield json.dumps({"type": "error", "detail": str(e)}, ensure_ascii=False).encode("utf-8") + b"\n"


class ApproveReq(BaseModel):
    decision_id: str
    approved: bool = False

@router.post("/api/chat/approve")
async def approve_tool_call(req: ApproveReq):
    ok = state._agent.engine.approve(req.decision_id, req.approved)
    if not ok:
        # Already resolved (double-click or timeout) — return 200 so frontend doesn't error
        logger.info(f"Approval {req.decision_id}: already resolved (duplicate or timeout)")
        return JSONResponse({"status": "ok", "decision_id": req.decision_id, "approved": req.approved, "note": "already resolved"})
    logger.info(f"Approval {req.decision_id}: {'approved' if req.approved else 'denied'}")
    return JSONResponse({"status": "ok", "decision_id": req.decision_id, "approved": req.approved})


@router.post("/api/chat/undo")
async def undo_chat(steps: int = 1, session_id: str = "default"):
    if steps < 1:
        return JSONResponse({"error": "steps must be >= 1"}, status_code=400)
    engine = state._agent.engine
    available = engine.checkpoint_count()
    if available < steps:
        return JSONResponse({"status": "insufficient_checkpoints", "available": available, "requested": steps})
    restored = engine.undo(steps, session_id=session_id)
    if restored is None:
        return JSONResponse({"status": "no_checkpoints"})
    await state._agent.state_store.put(session_id, restored)
    msg_count = len(restored.get("messages", []))
    remaining = engine.checkpoint_count()
    logger.info(f"Undo {steps} round(s): restored to {msg_count} messages, {remaining} checkpoints remain")
    return JSONResponse({"status": "undone", "steps": steps, "messages": msg_count, "remaining_checkpoints": remaining})


@router.get("/api/chat/undo/status")
async def undo_status():
    engine = state._agent.engine
    return JSONResponse({"available": engine.checkpoint_count(), "max": 3})

