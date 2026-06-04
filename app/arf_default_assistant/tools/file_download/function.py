"""file_download -- generate download URL for a workspace file."""
from pathlib import Path


async def execute(path: str, label: str = "", _workspace: str = "") -> dict:
    # path is pre-resolved to absolute. _workspace kept for containment
    # check and relative_to() — no path joining happens here.
    p = Path(path).resolve()
    ws = Path(_workspace).resolve() if _workspace else p.parent

    if not str(p).startswith(str(ws)):
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
