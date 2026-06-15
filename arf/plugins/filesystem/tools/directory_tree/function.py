"""directory_tree tool — recursive JSON tree of directory contents."""

import fnmatch
import os


def _build_tree(current_path: str, root_path: str, exclude_patterns: list[str]) -> list[dict]:
    entries = []
    try:
        with os.scandir(current_path) as it:
            for entry in it:
                relpath = os.path.relpath(os.path.join(current_path, entry.name), root_path)

                if _should_exclude(relpath, entry.is_dir(), exclude_patterns):
                    continue

                node: dict = {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                }

                if entry.is_dir():
                    node["children"] = _build_tree(
                        os.path.join(current_path, entry.name), root_path, exclude_patterns
                    )

                entries.append(node)
    except PermissionError:
        pass

    entries.sort(key=lambda x: (x["type"] != "directory", x["name"]))
    return entries


def _should_exclude(relpath: str, is_dir: bool, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(relpath, pat):
            return True
        if fnmatch.fnmatch(relpath, f"**/{pat}"):
            return True
        if is_dir and fnmatch.fnmatch(relpath, f"**/{pat}/**"):
            return True
    return False


DEFAULT_EXCLUDES = [".git", ".venv", "venv", "__pycache__", "*.pyc", "node_modules"]


async def execute(path: str, excludePatterns: list[str] | None = None, **kwargs) -> dict:
    if not os.path.isdir(path):
        return {"ok": False, "error": f"Not a directory: {path}"}

    exclude = list(DEFAULT_EXCLUDES) + (excludePatterns or [])
    tree = _build_tree(path, path, exclude)

    return {"ok": True, "tree": tree}
