"""search_files tool — recursive glob search."""

import fnmatch
import glob
import os

DEFAULT_EXCLUDES = [".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache", "*.pyc"]
MAX_RESULTS = 500


async def execute(
    path: str, pattern: str, excludePatterns: list[str] | None = None, **kwargs
) -> dict:
    if not os.path.isdir(path):
        return {"ok": False, "error": f"Not a directory: {path}"}

    excludes = (excludePatterns or []) + DEFAULT_EXCLUDES
    matches = []
    truncated = False

    try:
        if not pattern.startswith("**"):
            pattern = f"**/{pattern}"
        for rel in glob.glob(pattern, root_dir=path, recursive=True):
            if _matches_any(rel, excludes):
                continue
            full = os.path.join(path, rel)
            matches.append(full)
            if len(matches) >= MAX_RESULTS:
                truncated = True
                break
    except PermissionError:
        pass

    return {
        "ok": True,
        "matches": matches,
        "count": len(matches),
        "truncated": truncated,
    }


def _matches_any(name: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(name, pat):
            return True
    return False
