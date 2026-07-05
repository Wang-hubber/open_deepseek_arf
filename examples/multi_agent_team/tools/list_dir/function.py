"""List contents of a directory."""

from pathlib import Path


async def execute(path: str, _workspace: str) -> dict:
    p = Path(_workspace) / path
    if not p.is_dir():
        return {"ok": False, "error": f"not a directory: {p}"}
    try:
        entries = []
        for child in sorted(p.iterdir()):
            entries.append({"name": child.name, "is_dir": child.is_dir()})
        return {"ok": True, "entries": entries}
    except Exception as e:
        return {"ok": False, "error": str(e)}