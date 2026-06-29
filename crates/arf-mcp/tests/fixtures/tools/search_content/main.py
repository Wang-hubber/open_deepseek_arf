#!/usr/bin/env python3
import sys
import json
import re
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "target", ".mypy_cache", ".pytest_cache"}


def main():
    try:
        params = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"invalid JSON params: {e}"}))
        sys.exit(0)

    pattern_str = params.get("pattern", "")
    dir_str = params.get("path", "")

    if not pattern_str:
        print(json.dumps({"ok": False, "error": "missing required param: pattern"}))
        sys.exit(0)
    if not dir_str:
        print(json.dumps({"ok": False, "error": "missing required param: path"}))
        sys.exit(0)

    search_dir = Path(dir_str)
    if not search_dir.exists():
        print(json.dumps({"ok": False, "error": f"directory not found: {dir_str}"}))
        sys.exit(0)
    if not search_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"not a directory: {dir_str}"}))
        sys.exit(0)

    try:
        pattern = re.compile(pattern_str)
    except re.error as e:
        print(json.dumps({"ok": False, "error": f"invalid regex: {e}"}))
        sys.exit(0)

    matches = []
    max_matches = 100

    for file_path in search_dir.rglob("*"):
        if any(skip in file_path.parts for skip in SKIP_DIRS):
            continue

        if not file_path.is_file():
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue

        for line_no, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                matches.append({
                    "file": str(file_path),
                    "line": line_no,
                    "content": line.strip(),
                })
                if len(matches) >= max_matches:
                    print(json.dumps({
                        "ok": True,
                        "matches": matches,
                        "truncated": True,
                    }))
                    sys.exit(0)

    print(json.dumps({"ok": True, "matches": matches}))


if __name__ == "__main__":
    main()
