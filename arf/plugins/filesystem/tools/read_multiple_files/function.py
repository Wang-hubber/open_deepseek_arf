"""read_multiple_files tool — batch read files with per-file error tolerance."""

import asyncio
import os


async def _read_one(path: str) -> dict:
    try:
        if not os.path.isfile(path):
            return {"path": path, "ok": False, "error": f"Not a file: {path}"}
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"path": path, "ok": True, "content": content}
    except UnicodeDecodeError:
        return {"path": path, "ok": False, "error": "Cannot read as UTF-8 text"}
    except OSError as e:
        return {"path": path, "ok": False, "error": str(e)}


async def execute(paths: list[str], **kwargs) -> dict:
    if not paths:
        return {"ok": False, "error": "At least one path is required"}

    results = await asyncio.gather(*(_read_one(p) for p in paths))

    return {
        "ok": True,
        "results": results,
        "total": len(results),
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
    }
