"""resolve_conflict -- apply held changes to workspace."""
import json
import shutil
from pathlib import Path


async def execute(task_id: str, session_id: str = "", _engine=None) -> dict:
    ws = Path(getattr(_engine, '_workspace_dir', '.') if _engine else '.')
    data_dir = getattr(_engine, '_data_dir', './data') if _engine else './data'
    conflict_dir = Path(data_dir) / session_id / "conflicts" / task_id
    manifest_path = conflict_dir / "manifest.json"

    if not manifest_path.exists():
        return {"ok": False, "error": f"no held changes for task '{task_id}'"}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files_dir = conflict_dir / "files"

    applied = []
    for path in manifest["conflict_paths"]:
        src = files_dir / path
        if src.exists():
            dst = ws / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            applied.append(path)

    shutil.rmtree(conflict_dir, ignore_errors=True)

    return {
        "ok": True,
        "applied": applied,
        "message": f"Applied held changes from {task_id}: {', '.join(applied)}",
    }
