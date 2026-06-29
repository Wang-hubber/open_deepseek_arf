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
    content = params.get("content", "")
    if not path_str:
        print(json.dumps({"ok": False, "error": "missing required param: path"}))
        sys.exit(0)

    file_path = Path(path_str)

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(json.dumps({"ok": False, "error": f"permission denied creating parent: {file_path.parent}"}))
        sys.exit(0)
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"mkdir error: {e}"}))
        sys.exit(0)

    try:
        file_path.write_text(content, encoding="utf-8")
    except PermissionError:
        print(json.dumps({"ok": False, "error": f"permission denied: {path_str}"}))
        sys.exit(0)
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"write error: {e}"}))
        sys.exit(0)

    print(json.dumps({
        "ok": True,
        "path": path_str,
        "bytes": len(content.encode("utf-8")),
    }))


if __name__ == "__main__":
    main()
