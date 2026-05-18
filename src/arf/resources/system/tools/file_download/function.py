from pathlib import Path


def execute(path: str, label: str = "", _workspace_dir: str = "") -> dict:
    ws = Path(_workspace_dir) if _workspace_dir else Path.cwd()
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
