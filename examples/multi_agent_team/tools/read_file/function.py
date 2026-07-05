"""Read a text file from the workspace."""

from pathlib import Path


async def execute(path: str, _workspace: str) -> dict:
    p = Path(_workspace) / path
    if not p.is_file():
        return {"ok": False, "error": f"not a file: {p}"}
    try:
        content = p.read_text(encoding="utf-8")
        return {"ok": True, "content": content}
    except Exception as e:
        return {"ok": False, "error": str(e)}