from pathlib import Path


def execute(path: str) -> dict:
    p = Path(path)
    try:
        if not p.exists():
            return {"error": f"File not found: {path}"}
        if p.is_dir():
            return {"error": "Cannot delete directories: {path}"}
        deleted_path = p.with_name(p.name + "_deleted")
        p.rename(deleted_path)
        return {"ok": True, "path": str(p), "deleted_as": str(deleted_path)}
    except Exception as e:
        return {"error": str(e)}
