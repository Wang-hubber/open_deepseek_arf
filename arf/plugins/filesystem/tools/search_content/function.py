"""search_content tool — recursive file content search (grep)."""

import fnmatch
import os
import re

DEFAULT_EXCLUDES = [".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache", "*.pyc", "*.pyo"]

# File extensions likely to be text — everything else skipped
TEXT_EXTENSIONS: set[str] | None = None  # None = search all files


def _is_text_file(filepath: str) -> bool:
    """Heuristic: skip known binary extensions."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
               ".ico", ".svg", ".mp3", ".wav", ".ogg", ".flac",
               ".mp4", ".mov", ".avi", ".mkv", ".webm",
               ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
               ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
               ".ttf", ".otf", ".woff", ".woff2", ".eot",
               ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe",
               ".db", ".sqlite", ".sqlite3", ".class", ".jar"}:
        return False
    return True


def _matches_any(name: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


async def execute(
    pattern: str,
    path: str,
    regex: bool = False,
    include: str | None = None,
    excludePatterns: list[str] | None = None,
    maxResults: int = 100,
    **kwargs,
) -> dict:
    if not os.path.isdir(path):
        return {"ok": False, "error": f"Not a directory: {path}"}

    # Compile search pattern
    if regex:
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return {"ok": False, "error": f"Invalid regex: {e}"}
    else:
        compiled = re.compile(re.escape(pattern))

    # Build include/exclude filter lists
    include_globs: list[str] = []
    if include:
        include_globs = [g.strip() for g in include.split(",") if g.strip()]

    excludes = (excludePatterns or []) + DEFAULT_EXCLUDES

    matches: list[dict] = []
    truncated = False

    try:
        for dirpath, dirnames, filenames in os.walk(path):
            # Filter directories in-place
            dirnames[:] = [d for d in dirnames if not _matches_any(d, excludes)]

            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                relpath = os.path.relpath(filepath, path)

                # Exclude check
                if _matches_any(relpath, excludes):
                    continue

                # Include filter
                if include_globs and not any(
                    fnmatch.fnmatch(relpath, g) for g in include_globs
                ):
                    continue

                # Binary skip
                if not _is_text_file(filepath):
                    continue

                # Search file content
                try:
                    with open(filepath, "r", encoding="utf-8", errors="surrogateescape") as f:
                        for line_num, line in enumerate(f, 1):
                            if compiled.search(line):
                                matches.append({
                                    "file": relpath,
                                    "line": line.rstrip("\n\r"),
                                    "line_number": line_num,
                                })
                                if len(matches) >= maxResults:
                                    truncated = True
                                    break
                except (UnicodeDecodeError, PermissionError, OSError):
                    continue

                if truncated:
                    break

            if truncated:
                break
    except PermissionError:
        pass

    return {
        "ok": True,
        "pattern": pattern,
        "regex": regex,
        "matches": matches,
        "count": len(matches),
        "truncated": truncated,
    }
