"""undo -- roll back N interaction rounds (state + files)."""
from arf.agent.registry import get_agent


async def execute(steps: int = 1) -> dict:
    """Call the engine's undo mechanism and return status."""
    try:
        agent = get_agent()
        if agent is None:
            return {"ok": False, "error": "Agent not initialized yet"}

        engine = agent._engine
        effective_steps = steps + 1  # +1 for the checkpoint just pushed for this round
        available = engine.checkpoint_count()
        if available < effective_steps:
            return {
                "ok": False,
                "error": f"Only {available} checkpoints available, requested {steps}",
                "available": available,
            }

        # steps+1: skip the checkpoint just pushed for this undo round itself
        restored = engine.undo(steps + 1)
        if restored is None:
            return {"ok": False, "error": "No checkpoints available"}

        # Write restored state back to state store
        await agent.state_store.put("default", restored)
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
