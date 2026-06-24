"""task_complete — kernel tool for explicit task completion signal.

Returns a structured result with task_complete=True. Engine detects
this in _detect_primitives and calls TaskLifecycleProtocol.complete().
"""
import logging

logger = logging.getLogger("arf.skills.task_complete")


async def execute(
    summary: str = "",
    result: str = "",
    files_changed: dict[str, list[str]] | None = None,
    confidence: float = 1.0,
    notes: str = "",
    **kwargs,
) -> dict:
    return {
        "ok": True,
        "task_complete": True,
        "summary": summary,
        "result": result,
        "files_changed": files_changed or {},
        "confidence": max(0.0, min(1.0, confidence)),
        "notes": notes,
    }
