"""handoff — forward tasks or return results between agents."""
import json


async def execute(task: str = "", context: str = "") -> dict:
    return {
        "handoff": True,
        "task": task or "",
        "context": context or "",
    }
