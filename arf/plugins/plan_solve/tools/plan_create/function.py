"""plan_create — validate dependency graph and persist plan.json."""
import json
import time
from pathlib import Path

from arf.plugins.plan_solve.validation import validate_steps


async def execute(
    task: str,
    steps: list[dict],
    _engine=None,
    _workspace: str = "",
) -> dict:
    """Validate steps DAG and write plan.json to workspace."""
    if not task or not task.strip():
        return {"ok": False, "error": "task must be a non-empty string"}

    if not steps:
        return {"ok": False, "error": "steps list is empty", "suggestion": "Provide at least one step"}

    # Validate the dependency graph
    validation = validate_steps(steps)
    if not validation["ok"]:
        return validation

    # Initialize step metadata
    plan_id = f"plan-{int(time.time())}"
    now = time.time()
    initialized_steps = []
    for s in steps:
        initialized_steps.append({
            "index": s["index"],
            "description": s.get("description", ""),
            "tool_hint": s.get("tool_hint", ""),
            "status": "pending",
            "depends_on": s.get("depends_on", []),
            "blocks": s.get("blocks", []),
            "sub_session_id": None,
            "result": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
        })

    plan = {
        "plan_id": plan_id,
        "task": task.strip(),
        "status": "executing",
        "created_at": now,
        "updated_at": now,
        "steps": initialized_steps,
    }

    # Persist to workspace
    workspace = Path(_workspace) if _workspace else Path("workspace/default")
    workspace.mkdir(parents=True, exist_ok=True)
    plan_file = workspace / "plan.json"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2))

    return {
        "ok": True,
        "plan_id": plan_id,
        "task": task.strip(),
        "steps": initialized_steps,
        "count": len(initialized_steps),
    }
