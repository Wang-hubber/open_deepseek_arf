"""file_deleter -- async soft-delete files."""
from pathlib import Path

WORKSPACE = Path("workspaces/default")
USER_RESTRICTED_PREFIXES = ("/tools/", "/skills/", "/models/")


async def execute(path: str, _agent_mode: str = "sys") -> dict:
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if prefix in path or path.lstrip("/").startswith(prefix.strip("/") + "/"):
                return {
                    "error": (
                        f"User Agent cannot delete {path}. "
                        f"tools/, skills/, models/ paths require Sys Agent. "
                        f"Call handoff_to_sys to hand over."
                    )
                }

    p = WORKSPACE / path
    try:
        if not p.exists():
            return {"error": f"File not found: {path}"}
        if p.is_dir():
            return {"error": f"Cannot delete directories: {path}"}
        deleted_path = p.with_name(p.name + "_deleted")
        p.rename(deleted_path)
        return {"ok": True, "path": str(p), "deleted_as": str(deleted_path)}
    except Exception as e:
        return {"error": str(e)}


async def rollback(path: str, _agent_mode: str = "sys") -> dict:
    """Undo file_deleter: rename the _deleted file back to original name."""
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if prefix in path or path.lstrip("/").startswith(prefix.strip("/") + "/"):
                return {"ok": False, "error": f"User Agent cannot rollback {path}"}
    p = WORKSPACE / path
    deleted_path = p.with_name(p.name + "_deleted")
    try:
        if deleted_path.exists():
            deleted_path.rename(p)
            return {"ok": True, "action": "restored", "path": str(p)}
        return {"ok": True, "action": "nothing", "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
