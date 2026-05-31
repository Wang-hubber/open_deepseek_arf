"""grep — regex search across files in a directory."""
import fnmatch
import re
from pathlib import Path

WORKSPACE = Path("workspace/default")


def _resolve_workspace(workspace: str) -> Path:
    return Path(workspace) if workspace else WORKSPACE
DEFAULT_EXCLUDES = [".git", "__pycache__", "node_modules", ".venv", "*.pyc", "*.pyo"]
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".class", ".jar",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv",
    ".ttf", ".otf", ".woff", ".woff2",
    ".db", ".sqlite", ".sqlite3",
}


def _is_binary(filepath: Path) -> bool:
    if filepath.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        sample = filepath.read_bytes()[:1024]
        return b"\x00" in sample
    except OSError:
        return True


def _path_matches_globs(p: Path, patterns: list[str]) -> bool:
    name = p.name
    for pat in patterns:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


async def execute(
    pattern: str,
    path: str = ".",
    include: str | None = None,
    exclude: str | None = None,
    max_results: int = 50,
    _workspace: str = "",
) -> dict:
    ws = _resolve_workspace(_workspace)
    search_root = ws / path
    if not search_root.exists():
        return {"ok": False, "error": f"Directory not found: {path}"}
    if not search_root.is_dir():
        return {"ok": False, "error": f"Not a directory: {path}"}

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"Invalid regex pattern: {e}"}

    include_pats = [p.strip() for p in include.split(",") if p.strip()] if include else []
    exclude_pats = [p.strip() for p in exclude.split(",") if p.strip()] if exclude else []
    effective_excludes = exclude_pats if exclude_pats else DEFAULT_EXCLUDES

    results = []
    dir_excludes = {d for d in effective_excludes if not d.startswith("*")}
    try:
        for filepath in sorted(search_root.rglob("*")):
            if not filepath.is_file():
                continue
            if filepath.suffix.lower() in BINARY_EXTENSIONS:
                continue

            if include_pats and not _path_matches_globs(filepath, include_pats):
                continue
            if _path_matches_globs(filepath, effective_excludes):
                continue

            if any(d in filepath.parts for d in dir_excludes):
                continue

            if _is_binary(filepath):
                continue

            try:
                lines = filepath.read_text(encoding="utf-8").split("\n")
            except (UnicodeDecodeError, OSError):
                continue

            rel_path = str(filepath.relative_to(ws))
            for line_num, line in enumerate(lines, 1):
                if regex.search(line):
                    results.append({
                        "file": rel_path,
                        "line": line.rstrip("\n"),
                        "line_number": line_num,
                    })
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break
    except Exception as e:
        return {"ok": False, "error": f"Search error: {e}"}

    return {
        "ok": True,
        "pattern": pattern,
        "matches": results,
        "count": len(results),
        "truncated": len(results) >= max_results,
    }
