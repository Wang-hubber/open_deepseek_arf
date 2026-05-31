"""file_download -- generate download URL for a workspace file."""
from pathlib import Path

WORKSPACE = Path("workspaces/default")


async def execute(path: str, label: str = "", _workspace: str = "") -> dict:
    ws = Path(_workspace) if _workspace else WORKSPACE
    p = (ws / path).resolve()

    if not str(p).startswith(str(ws.resolve())):
        return {"error": f"Path escapes workspace: {path}"}

    if not p.exists():
        return {"error": f"File not found: {path}"}

    if p.is_dir():
        return {"error": f"Cannot download directory: {path}"}

    try:
        rel = str(p.relative_to(ws))
        display = label or p.name
        return {
            "ok": True,
            "path": rel,
            "filename": p.name,
            "label": display,
            "size": p.stat().st_size,
            "download_url": f"/api/download?file={rel}",
        }
    except Exception as e:
        return {"error": str(e)}
