"""cancel_held -- discard held changes from conflict storage."""
import shutil
from pathlib import Path


async def execute(task_id: str, session_id: str = "", _engine=None) -> dict:
    data_dir = getattr(_engine, '_data_dir', './data') if _engine else './data'
    conflict_dir = Path(data_dir) / session_id / "conflicts" / task_id

    if not conflict_dir.exists():
        return {"ok": False, "error": f"no held changes for task '{task_id}'"}

    shutil.rmtree(conflict_dir, ignore_errors=True)
    return {"ok": True, "discarded": True, "message": f"Discarded held changes from {task_id}"}
