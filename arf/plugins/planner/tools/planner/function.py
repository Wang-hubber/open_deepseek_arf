"""planner — task decomposition via system_model, with optional todo integration."""
import json
import time


async def execute(task: str = "", confirm: bool = False, _engine=None) -> dict:
    """Decompose a task into ordered steps using the system model."""
    if not task or not task.strip():
        return {"error": "task must be a non-empty string"}

    try:
        # Build context from recent conversation
        messages = []
        if _engine is not None and hasattr(_engine, '_last_state'):
            msgs = _engine._last_state.get("messages", [])
            user_msgs = [m.get("content", "") for m in msgs[-6:] if m.get("role") == "user"]
            messages = user_msgs[-3:]

        context = "\n".join(messages)

        prompt = (
            "You are a task planner. Given a user's goal and conversation context, "
            "decompose the goal into an ordered list of concrete, actionable steps.\n\n"
            f"## Conversation Context\n{context}\n\n"
            f"## Goal\n{task}\n\n"
            "## Instructions\n"
            "1. Produce 2-7 steps in logical order\n"
            "2. Each step must be a single action one tool can accomplish\n"
            "3. Identify dependencies between steps (by index, 1-based)\n"
            "4. Return ONLY valid JSON:\n"
            '{"steps": [{"index": 1, "description": "...", "tool_hint": "...", "depends_on": []}]}\n'
        )

        if _engine is not None and hasattr(_engine, '_call_model'):
            response = await _engine._call_model(
                [{"role": "user", "content": prompt}],
                model_name=getattr(_engine, '_system_model_name', ''),
            )
            content = response.get("content", "") if isinstance(response, dict) else str(response)
            try:
                plan = json.loads(content)
            except json.JSONDecodeError:
                plan = {"steps": [{"index": 1, "description": task}]}
        else:
            plan = {"steps": [{"index": 1, "description": task}]}

        steps = plan.get("steps", [])
        plan_id = f"plan-{int(time.time())}"

        result = {
            "ok": True,
            "task": task,
            "plan_id": plan_id,
            "steps": steps,
            "count": len(steps),
        }

        # If confirmed, materialize steps as todo tasks
        if confirm and steps and _engine is not None:
            from pathlib import Path
            workspace = Path("workspace/default")
            tasks_file = workspace / "tasks.json"

            # Load existing tasks to compute next ID
            existing_ids = []
            if tasks_file.exists():
                data = json.loads(tasks_file.read_text())
                existing_ids = [int(t["id"]) for t in data.get("tasks", [])]

            next_id = max(existing_ids, default=0) + 1
            todo_ids = []

            # index_to_id maps step index -> todo task id
            index_to_id: dict[int, str] = {}

            # First pass: create all tasks
            for step in steps:
                tid = str(next_id)
                next_id += 1
                index_to_id[step["index"]] = tid
                task_entry = {
                    "id": tid,
                    "subject": step["description"],
                    "description": f"Tool hint: {step.get('tool_hint', 'any')}",
                    "status": "pending",
                    "metadata": {"plan_id": plan_id, "step_index": step["index"]},
                    "blockedBy": [],
                    "created_at": time.time(),
                    "updated_at": time.time(),
                }

                # Load current state to append task
                data = {"tasks": [], "last_updated_round": 0}
                if tasks_file.exists():
                    data = json.loads(tasks_file.read_text())
                data["tasks"].append(task_entry)
                if hasattr(_engine, "_interaction_round"):
                    data["last_updated_round"] = _engine._interaction_round
                tasks_file.parent.mkdir(parents=True, exist_ok=True)
                tasks_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                todo_ids.append(tid)

            # Second pass: wire dependencies
            for step in steps:
                if step.get("depends_on"):
                    tid = index_to_id[step["index"]]
                    data = json.loads(tasks_file.read_text())
                    for t in data["tasks"]:
                        if t["id"] == tid:
                            for dep_idx in step["depends_on"]:
                                dep_id = index_to_id.get(dep_idx)
                                if dep_id and dep_id not in t["blockedBy"]:
                                    t["blockedBy"].append(dep_id)
                            break
                    tasks_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

            # Persist plan
            plan_file = workspace / "plan.json"
            plan_record = {
                "plan_id": plan_id,
                "task": task,
                "status": "executing",
                "steps": steps,
                "todo_ids": todo_ids,
                "created_at": time.time(),
            }
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(json.dumps(plan_record, ensure_ascii=False, indent=2))

            result["todo_ids"] = todo_ids
            result["plan_file"] = str(plan_file)

        return result

    except Exception as e:
        return {"error": str(e)}
