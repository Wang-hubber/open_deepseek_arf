"""cancel_held -- discard held changes from conflict storage."""
import shutil
from pathlib import Path

from arf.plugins.a2a_subagents.tools import _registry


async def execute(task_id: str) -> dict:
    session_id = _registry.current_session_id
    data_dir = _registry.data_dir
    conflict_dir = Path(data_dir) / session_id / "conflicts" / task_id

    if not conflict_dir.exists():
        return {"ok": False, "error": f"no held changes for task '{task_id}'"}

    shutil.rmtree(conflict_dir, ignore_errors=True)
    return {"ok": True, "discarded": True, "message": f"Discarded held changes from {task_id}"}
