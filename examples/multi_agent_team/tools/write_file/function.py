"""Write text to a file in the workspace."""

from pathlib import Path


async def execute(path: str, content: str, _workspace: str) -> dict:
    p = Path(_workspace) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}