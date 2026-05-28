"""undo -- roll back N interaction rounds (state + files)."""


async def execute(steps: int = 1, _engine=None, _state_store=None) -> dict:
    """Call the engine's undo mechanism and return status."""
    try:
        if _engine is None:
            return {"ok": False, "error": "Engine not available"}

        available = _engine.checkpoint_count()
        if available < steps:
            return {
                "ok": False,
                "error": f"Only {available} checkpoints available, requested {steps}",
                "available": available,
            }

        restored = _engine.undo(steps, session_id="default")
        if restored is None:
            return {"ok": False, "error": "No checkpoints available"}

        if _state_store:
            await _state_store.put("default", restored)
        msg_count = len(restored.get("messages", []))
        remaining = _engine.checkpoint_count()

        return {
            "ok": True,
            "steps": steps,
            "messages_restored": msg_count,
            "remaining_checkpoints": remaining,
            "note": "Conversation and files have been rolled back. Continue from here."
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
