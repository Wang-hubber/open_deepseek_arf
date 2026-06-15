"""read_multiple_files tool — batch read files with per-file error tolerance."""
import asyncio
import os


async def _read_one(path: str, encoding: str = "utf-8") -> dict:
    try:
        if not os.path.isfile(path):
            return {"path": path, "ok": False, "error": f"Not a file: {path}"}
        with open(path, "r", encoding=encoding) as f:
            content = f.read()
        return {"path": path, "ok": True, "content": content}
    except (UnicodeDecodeError, LookupError) as e:
        return {"path": path, "ok": False, "error": f"Cannot read as {encoding} text — {e}"}
    except OSError as e:
        return {"path": path, "ok": False, "error": str(e)}


async def execute(paths: list[str], encoding: str = "utf-8", **kwargs) -> dict:
    if not paths:
        return {"ok": False, "error": "At least one path is required"}

    results = await asyncio.gather(*(_read_one(p, encoding) for p in paths))

    return {
        "ok": True,
        "results": results,
        "total": len(results),
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
    }
