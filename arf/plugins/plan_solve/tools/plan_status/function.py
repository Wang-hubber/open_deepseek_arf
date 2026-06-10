"""plan_status — read-only plan progress snapshot."""
import json
from pathlib import Path


async def execute(
    _engine=None,
    _workspace: str = "",
) -> dict:
    """Return current plan progress."""
    workspace = Path(_workspace) if _workspace else Path("workspace/default")
    plan_file = workspace / "plan.json"

    if not plan_file.exists():
        return {"ok": False, "error": f"no plan found at {workspace}"}

    plan = json.loads(plan_file.read_text())
    return {
        "ok": True,
        "plan_id": plan["plan_id"],
        "task": plan["task"],
        "status": plan["status"],
        "created_at": plan.get("created_at"),
        "updated_at": plan.get("updated_at"),
        "steps": plan.get("steps", []),
        "count": len(plan.get("steps", [])),
    }
