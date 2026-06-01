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


@router.post("/api/chat")
async def chat(req: ChatReq):
    if req.stream:
        return StreamingResponse(
            _sse_chat(req.message),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        result = await state._agent.chat(req.message)
        return JSONResponse({"content": result})
    except Exception as e:
        return JSONResponse({"content": "", "error": str(e)}, status_code=500)


async def _sse_chat(message: str):
    cancel_evt = asyncio.Event()
    state._active_cancel_events["default"] = cancel_evt
    state._agent.engine.set_cancel_event(cancel_evt)

    yield ":" + " " * 2048 + "\n\n"

    try:
        async for event in state._agent.astream(message):
            t = event.type
            if t == "thinking_delta":
                chunk = {"type": "chunk", "content": event.data.get("content", "")}
                if event.data.get("reasoning"):
                    chunk["reasoning"] = event.data["reasoning"]
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)
            elif t == "tool_call_chunk":
                yield f"data: {json.dumps({'type': 'tool_call_streaming', 'name': event.data.get('name', ''), 'arguments': event.data.get('arguments', ''), 'id': event.data.get('id', ''), 'delta': event.data.get('delta', '')}, ensure_ascii=False)}\n\n"
            elif t == "tool_call_start":
                yield f"data: {json.dumps({'type': 'tool_call', 'name': event.data.get('tool_name', ''), 'arguments': event.data.get('arguments', '{}'), 'id': event.data.get('id', 'call_0')}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)  # flush before blocking tool execution
            elif t == "tool_call_end":
                success = event.data.get("success", False)
                yield f"data: {json.dumps({'type': 'tool_result', 'id': event.data.get('id', event.data.get('tool_name', 'call_0')), 'result': 'success' if success else 'error', 'tool': event.data.get('tool_name', ''), 'content': event.data.get('result', '') if success else '', 'error_msg': event.data.get('error', '')}, ensure_ascii=False)}\n\n"
            elif t == "approval_required":
                yield f"data: {json.dumps({'type': 'approval_required', 'decision_id': event.data.get('decision_id', ''), 'tool_name': event.data.get('tool_name', ''), 'params': event.data.get('params', {})}, ensure_ascii=False)}\n\n"
            elif t == "approval_resolved":
                yield f"data: {json.dumps({'type': 'approval_resolved', 'decision_id': event.data.get('decision_id', ''), 'tool_name': event.data.get('tool_name', ''), 'approved': event.data.get('approved', False), 'reason': event.data.get('reason', '')}, ensure_ascii=False)}\n\n"
            elif t == "guard_block":
                yield f"data: {json.dumps({'type': 'guard_block', 'tool_name': event.data.get('tool_name', ''), 'guard': event.data.get('guard', ''), 'reason': event.data.get('reason', '')}, ensure_ascii=False)}\n\n"
            elif t == "guard_pass":
                yield f"data: {json.dumps({'type': 'guard_pass', 'tool_name': event.data.get('tool_name', '')}, ensure_ascii=False)}\n\n"
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

        state_data = await state._agent.state_store.get("default")
        history = state_data.get("messages", []) if state_data else []
        last = ""
        for m in reversed(history):
            if m.get("role") == "assistant":
                last = m.get("content", "")
                break
        yield f"data: {json.dumps({'type': 'done', 'response': last, 'history': history, 'session_id': 'default'}, ensure_ascii=False)}\n\n"
    except asyncio.CancelledError:
        cancel_evt.set()
        logger.info("SSE client disconnected, cancelling agent")
    except Exception as e:
        import traceback
        logger.error(f"SSE chat error: {traceback.format_exc()}")
        yield f"data: {json.dumps({'type': 'error', 'detail': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        state._active_cancel_events.pop("default", None)
        state._agent.engine.set_cancel_event(None)
        cancel_evt.clear()


@router.post("/api/chat/cancel")
async def cancel_chat():
    evt = state._active_cancel_events.get("default")
    if evt and not evt.is_set():
        evt.set()
        logger.info("Chat cancelled via API")
        return JSONResponse({"status": "cancelled"})
    return JSONResponse({"status": "no_active_chat"})


class ApproveReq(BaseModel):
    decision_id: str
    approved: bool = False

@router.post("/api/chat/approve")
async def approve_tool_call(req: ApproveReq):
    ok = state._agent.engine.approve(req.decision_id, req.approved)
    if not ok:
        return JSONResponse({"error": f"unknown decision_id: {req.decision_id}"}, status_code=404)
    logger.info(f"Approval {req.decision_id}: {'approved' if req.approved else 'denied'}")
    return JSONResponse({"status": "ok", "decision_id": req.decision_id, "approved": req.approved})


@router.post("/api/chat/undo")
async def undo_chat(steps: int = 1):
    if steps < 1:
        return JSONResponse({"error": "steps must be >= 1"}, status_code=400)
    engine = state._agent.engine
    available = engine.checkpoint_count()
    if available < steps:
        return JSONResponse({"status": "insufficient_checkpoints", "available": available, "requested": steps})
    restored = engine.undo(steps, session_id="default")
    if restored is None:
        return JSONResponse({"status": "no_checkpoints"})
    await state._agent.state_store.put("default", restored)
    msg_count = len(restored.get("messages", []))
    remaining = engine.checkpoint_count()
    logger.info(f"Undo {steps} round(s): restored to {msg_count} messages, {remaining} checkpoints remain")
    return JSONResponse({"status": "undone", "steps": steps, "messages": msg_count, "remaining_checkpoints": remaining})


@router.get("/api/chat/undo/status")
async def undo_status():
    engine = state._agent.engine
    return JSONResponse({"available": engine.checkpoint_count(), "max": 3})

