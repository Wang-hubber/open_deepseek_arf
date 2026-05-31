"""glob — find files by pattern matching."""
from pathlib import Path

WORKSPACE = Path("workspace/default")
MAX_RESULTS = 200
DEFAULT_EXCLUDES = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}


async def execute(pattern: str, path: str = ".") -> dict:
    search_root = WORKSPACE / path
    if not search_root.exists():
        return {"ok": False, "error": f"Directory not found: {path}"}
    if not search_root.is_dir():
        return {"ok": False, "error": f"Not a directory: {path}"}

    results = []
    try:
        for filepath in sorted(search_root.rglob(pattern)):
            if any(part in DEFAULT_EXCLUDES for part in filepath.parts):
                continue
            rel_path = filepath.relative_to(WORKSPACE)
            entry = {
                "path": str(rel_path),
                "type": "dir" if filepath.is_dir() else "file",
            }
            if filepath.is_file():
                try:
                    entry["size"] = filepath.stat().st_size
                except OSError:
                    entry["size"] = 0
            results.append(entry)
            if len(results) >= MAX_RESULTS:
                break
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "pattern": pattern,
        "matches": results,
        "count": len(results),
        "truncated": len(results) >= MAX_RESULTS,
    }
