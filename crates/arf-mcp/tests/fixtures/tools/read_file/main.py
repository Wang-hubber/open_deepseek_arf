#!/usr/bin/env python3
import sys
import json
from pathlib import Path


def main():
    try:
        params = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"invalid JSON params: {e}"}))
        sys.exit(0)

    path_str = params.get("path", "")
    if not path_str:
        print(json.dumps({"ok": False, "error": "missing required param: path"}))
        sys.exit(0)

    file_path = Path(path_str)
    if not file_path.exists():
        print(json.dumps({"ok": False, "error": f"file not found: {path_str}"}))
        sys.exit(0)

    if not file_path.is_file():
        print(json.dumps({"ok": False, "error": f"not a file: {path_str}"}))
        sys.exit(0)

    try:
        content = file_path.read_text(encoding="utf-8")
    except PermissionError:
        print(json.dumps({"ok": False, "error": f"permission denied: {path_str}"}))
        sys.exit(0)
    except UnicodeDecodeError:
        print(json.dumps({"ok": False, "error": f"binary file not supported: {path_str}"}))
        sys.exit(0)
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"read error: {e}"}))
        sys.exit(0)

    print(json.dumps({"ok": True, "content": content, "path": path_str}))


if __name__ == "__main__":
    main()
