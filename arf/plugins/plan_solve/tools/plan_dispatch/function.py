"""plan_dispatch — execute a single plan step in an isolated sub-agent."""
import json
import time
from pathlib import Path

from arf.engine.control_plane import ControlPlane
from arf.engine.checkpoint import InMemoryStateStore
from arf.event_bus import InMemoryEventBus


async def execute(
    step_index: int,
    prompt_override: str = "",
    _engine=None,
    _workspace: str = "",
) -> dict:
    """Execute a plan step after validating its dependencies are met."""
    workspace = Path(_workspace) if _workspace else Path("workspace/default")
    plan_file = workspace / "plan.json"

    if not plan_file.exists():
        return {"ok": False, "error": f"no plan found at {workspace}"}

    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    step = _find_step(plan, step_index)
    if step is None:
        return {"ok": False, "error": f"step {step_index} not found in plan"}

    # Precondition: dependencies must be done
    blocked = [d for d in step.get("depends_on", []) if _step_status(plan, d) != "done"]
    if blocked:
        return {
            "ok": False,
            "error": f"step {step_index} is blocked: depends on steps {blocked} which are not done",
            "blocked_by": blocked,
        }

    if step["status"] != "pending":
        return {"ok": False, "error": f"step {step_index} is already {step['status']}"}

    # Mark running
    step["status"] = "running"
    step["started_at"] = time.time()
    plan["updated_at"] = time.time()
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    # Emit started event (if engine has event_bus)
    if _engine and hasattr(_engine, "event_bus") and _engine.event_bus:
        from arf.core.events import AgentEvent
        _engine.event_bus.emit(AgentEvent(
            type="plan_step_started",
            data={"plan_id": plan["plan_id"], "step_index": step_index, "description": step["description"]},
        ))

    # Build and run sub-engine
    prompt = prompt_override.strip() or step["description"]
    session_id = f"plan_step_{plan['plan_id']}_{step_index}_{int(time.time() * 1000)}"
    sub_state_store = InMemoryStateStore()
    sub_event_bus = InMemoryEventBus()
    sub_engine = ControlPlane(
        max_turns=10,
        state_store=sub_state_store,
        tool_executor=_engine.tool_executor if _engine else None,
        event_bus=sub_event_bus,
        call_model=_engine._call_model if _engine else None,
        workspace_dir=str(workspace),
    )

    state = {
        "session_id": session_id,
        "agent_name": "plan_step",
        "messages": [{"role": "user", "content": prompt}],
        "current_model": "",
        "current_turn": 0,
        "interaction_round": 0,
        "context_summary": "",
        "tool_results": {},
        "plan": None,
        "metadata": {},
        "session_active": True,
    }

    try:
        result_state = await sub_engine.invoke(state)
    except Exception as exc:
        step["status"] = "failed"
        step["error"] = str(exc)
        step["finished_at"] = time.time()
        plan["updated_at"] = time.time()
        plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": False, "error": f"sub-agent crashed: {exc}", "step_index": step_index}

    # Extract result from last assistant message
    content = ""
    for m in reversed(result_state.get("messages", [])):
        if m.get("role") == "assistant" and m.get("content", "").strip():
            content = m["content"].strip()
            break

    step["status"] = "done"
    step["result"] = {"content": content}
    step["sub_session_id"] = session_id
    step["finished_at"] = time.time()
    plan["updated_at"] = time.time()
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    # Emit finished event
    if _engine and hasattr(_engine, "event_bus") and _engine.event_bus:
        from arf.core.events import AgentEvent
        _engine.event_bus.emit(AgentEvent(
            type="plan_step_finished",
            data={"plan_id": plan["plan_id"], "step_index": step_index, "status": "done", "content": content},
        ))

    return {"ok": True, "content": content, "step_index": step_index, "session_id": session_id}


def _find_step(plan: dict, step_index: int) -> dict | None:
    for s in plan.get("steps", []):
        if s["index"] == step_index:
            return s
    return None


def _step_status(plan: dict, step_index: int) -> str:
    s = _find_step(plan, step_index)
    return s["status"] if s else "unknown"
