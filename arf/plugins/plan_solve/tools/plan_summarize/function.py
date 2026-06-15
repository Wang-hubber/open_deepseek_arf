"""plan_summarize — validate all steps done/failed, then call model to summarize."""
import json
import time
from pathlib import Path


async def execute(
    _engine=None,
    _workspace: str = "",
) -> dict:
    """Summarize plan results after all steps complete."""
    workspace = Path(_workspace) if _workspace else Path("workspace/default")
    plan_file = workspace / "plan.json"

    if not plan_file.exists():
        return {"ok": False, "error": f"no plan found at {workspace}"}

    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    steps = plan.get("steps", [])

    pending_steps = [s["index"] for s in steps if s["status"] in ("pending", "running")]
    if pending_steps:
        return {
            "ok": False,
            "error": f"cannot summarize: steps {pending_steps} are still pending or running",
            "pending_steps": pending_steps,
        }

    # Build context from step results
    results_text = ""
    for s in steps:
        content = ""
        if s.get("result") and isinstance(s["result"], dict):
            content = s["result"].get("content", "")
        results_text += f"\n## Step {s['index']}: {s['description']} ({s['status']})\n{content}\n"

    prompt = (
        f"## Task: {plan['task']}\n\n"
        f"Below are the results of each step. Synthesize a clear, concise summary:\n"
        f"{results_text}\n\n"
        f"Provide the final result as a single coherent response."
    )

    if _engine is not None and hasattr(_engine, '_call_model'):
        response = await _engine._call_model(
            [{"role": "user", "content": prompt}],
            model_name="",
        )
        summary = response.get("content", "") if isinstance(response, dict) else str(response)
    else:
        summary = results_text

    plan["status"] = "done"
    plan["updated_at"] = time.time()
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2))

    return {"ok": True, "summary": summary.strip(), "plan_id": plan["plan_id"]}
