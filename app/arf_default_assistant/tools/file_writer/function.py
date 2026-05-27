"""file_writer -- async write file with content."""
from pathlib import Path

WORKSPACE = Path("workspaces/default")
USER_RESTRICTED_PREFIXES = ("/tools/", "/skills/", "/models/")


async def execute(path: str, content: str, _agent_mode: str = "sys") -> dict:
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if prefix in path or path.lstrip("/").startswith(prefix.strip("/") + "/"):
                return {
                    "error": (
                        f"User Agent cannot write to {path}. "
                        f"tools/, skills/, models/ paths require Sys Agent. "
                        f"Call handoff_to_sys to hand over."
                    )
                }

    p = WORKSPACE / path
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

        preview = content[:600]
        if len(content) > 600:
            preview += f"\n... ({len(content) - 600} more chars)"

        return {
            "ok": True,
            "path": str(p),
            "filename": p.name,
            "bytes": len(content),
            "preview": preview,
        }
    except Exception as e:
        return {"error": str(e)}


async def rollback(path: str, content: str = "", _agent_mode: str = "sys") -> dict:
    """Undo file_writer: delete the file that was (possibly) created/overwritten."""
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if prefix in path or path.lstrip("/").startswith(prefix.strip("/") + "/"):
                return {"ok": False, "error": f"User Agent cannot rollback {path}"}
    p = WORKSPACE / path
    try:
        if p.exists():
            p.unlink()
            return {"ok": True, "action": "deleted", "path": str(p)}
        return {"ok": True, "action": "nothing", "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
