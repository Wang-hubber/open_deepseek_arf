"""list_directory tool — list directory contents."""

import os


async def execute(path: str, **kwargs) -> dict:
    if not os.path.isdir(path):
        return {"ok": False, "error": f"Not a directory: {path}"}

    try:
        entries = []
        with os.scandir(path) as it:
            for entry in it:
                prefix = "[DIR]" if entry.is_dir() else "[FILE]"
                entries.append(f"{prefix} {entry.name}")
        entries.sort()
    except OSError as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "entries": entries, "count": len(entries)}
