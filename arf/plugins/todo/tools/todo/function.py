"""todo — structured task list with JSON persistence."""
import json
import time
from pathlib import Path

_FALLBACK_WORKSPACE = Path("workspace/default")


def _resolve_workspace(workspace: str) -> Path:
    return Path(workspace) if workspace else _FALLBACK_WORKSPACE


def _get_tasks_file(workspace: str = "") -> Path:
    return _resolve_workspace(workspace) / "tasks.json"


def _load_tasks(workspace: str = "") -> dict:
    tasks_file = _get_tasks_file(workspace)
    if not tasks_file.exists():
        return {"tasks": [], "last_updated_round": 0}
    return json.loads(tasks_file.read_text(encoding="utf-8"))


def _save_tasks(data: dict, _engine=None, _workspace: str = "") -> None:
    tasks_file = _get_tasks_file(_workspace)
    if _engine is not None and hasattr(_engine, "_interaction_round"):
        data["last_updated_round"] = _engine._interaction_round
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    tasks_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _task_summary(t: dict) -> dict:
    return {"id": t["id"], "subject": t["subject"], "status": t["status"]}


async def execute(
    action: str,
    id: str | None = None,
    subject: str | None = None,
    description: str | None = None,
    status: str | None = None,
    addBlocks: list[str] | None = None,
    addBlockedBy: list[str] | None = None,
    filter_status: str | None = None,
    metadata: dict | None = None,
    _engine=None,
    _workspace: str = "",
) -> dict:
    try:
        data = _load_tasks(_workspace)
        tasks: list[dict] = data["tasks"]

        if action == "create":
            if not subject:
                return {"ok": False, "error": "subject is required for create"}
            new_id = str(max((int(t["id"]) for t in tasks), default=0) + 1)
            task = {
                "id": new_id,
                "subject": subject,
                "description": description or "",
                "status": "pending",
                "metadata": metadata or {},
                "blockedBy": [],
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            tasks.append(task)
            _save_tasks(data, _engine, _workspace)
            return {"ok": True, "task": task}

        elif action == "update":
            if not id:
                return {"ok": False, "error": "id is required for update"}
            task = next((t for t in tasks if t["id"] == id), None)
            if task is None:
                return {"ok": False, "error": f"Task {id} not found"}
            if task["status"] == "deleted":
                return {"ok": False, "error": f"Task {id} is deleted"}

            if status is not None:
                if status not in ("pending", "in_progress", "completed"):
                    return {"ok": False, "error": f"Invalid status: {status}"}
                task["status"] = status
                task["updated_at"] = time.time()

            all_ids = {t["id"] for t in tasks if t["status"] != "deleted"}
            if addBlocks is not None:
                if id in addBlocks:
                    return {"ok": False, "error": "A task cannot block itself"}
                for bid in addBlocks:
                    if bid not in all_ids:
                        return {"ok": False, "error": f"Block target task {bid} not found"}
                task.setdefault("blocks", [])
                for bid in addBlocks:
                    if bid not in task["blocks"]:
                        task["blocks"].append(bid)
                task["updated_at"] = time.time()

            if addBlockedBy is not None:
                if id in addBlockedBy:
                    return {"ok": False, "error": "A task cannot depend on itself"}
                for bid in addBlockedBy:
                    if bid not in all_ids:
                        return {"ok": False, "error": f"Dependency task {bid} not found"}
                for bid in addBlockedBy:
                    if bid not in task["blockedBy"]:
                        task["blockedBy"].append(bid)
                task["updated_at"] = time.time()

            _save_tasks(data, _engine, _workspace)
            return {"ok": True, "task": task}

        elif action == "get":
            if not id:
                return {"ok": False, "error": "id is required for get"}
            task = next((t for t in tasks if t["id"] == id), None)
            if task is None or task["status"] == "deleted":
                return {"ok": False, "error": f"Task {id} not found"}
            return {"ok": True, "task": task}

        elif action == "list":
            visible = [t for t in tasks if t["status"] != "deleted"]
            if filter_status:
                visible = [t for t in visible if t["status"] == filter_status]
            return {
                "ok": True,
                "tasks": [_task_summary(t) for t in visible],
                "count": len(visible),
            }

        elif action == "delete":
            if not id:
                return {"ok": False, "error": "id is required for delete"}
            task = next((t for t in tasks if t["id"] == id), None)
            if task is None:
                return {"ok": False, "error": f"Task {id} not found"}
            if task["status"] not in ("pending", "completed"):
                return {"ok": False, "error": f"Cannot delete task with status '{task['status']}'. Only pending or completed tasks can be deleted."}
            task["status"] = "deleted"
            task["updated_at"] = time.time()
            _save_tasks(data, _engine, _workspace)
            return {"ok": True, "deleted": id}

        else:
            return {"ok": False, "error": f"Unknown action: {action}"}

    except Exception as e:
        return {"ok": False, "error": str(e)}
