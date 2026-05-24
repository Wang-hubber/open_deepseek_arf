"""undo -- roll back N interaction rounds (state + files)."""
import asyncio, json
from pathlib import Path

async def execute(steps: int = 1) -> dict:
    """Call the engine's undo mechanism and return status."""
    # Access the agent via a well-known path — the engine is bound at startup
    try:
        # We find the agent by importing server module
        from server import _agent
        engine = _agent._engine

        available = engine.checkpoint_count()
        if available < steps:
            return {
                "ok": False,
                "error": f"Only {available} checkpoints available, requested {steps}",
                "available": available,
            }

        restored = engine.undo(steps)
        if restored is None:
            return {"ok": False, "error": "No checkpoints available"}

        # Write restored state back to state store
        await _agent.state_store.put("default", restored)
        msg_count = len(restored.get("messages", []))
        remaining = engine.checkpoint_count()

        return {
            "ok": True,
            "steps": steps,
            "messages_restored": msg_count,
            "remaining_checkpoints": remaining,
            "note": "Conversation and files have been rolled back. Continue from here."
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
