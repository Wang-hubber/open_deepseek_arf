"""handoff -- structured handoff to SysAgent."""
import json


async def execute(task: str, context: str = "") -> dict:
    return {
        "handoff": True,
        "task": task,
        "context": context,
    }
