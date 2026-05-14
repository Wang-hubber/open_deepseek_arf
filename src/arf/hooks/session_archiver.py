"""session_archiver hook -- persists ended session to disk.

Triggered on SessionEnd.

Usage:
    echo '{"conversation": [...], "session_start": "2026-05-13T14:30:25+00:00"}' \
    | python -m arf.hooks.session_archiver

Context:
    Environment: ARF_HOOK_WORKSPACE, ARF_HOOK_SESSION_ID, ARF_HOOK_SESSION_TITLE
    Stdin: {"event": "SessionEnd", "payload": {"session_id": "...", "session_title": "..."},
            "data": {"conversation": [...], "session_start": "...", "message_count": N}}

Output:
    stdout: {"archived": true, "session_id": "20260513_143025"}
    exit 0
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_ARCHIVES = 10


def _evict_oldest(sessions_dir: Path) -> None:
    files = sorted(sessions_dir.glob("*.json"))
    while len(files) >= MAX_ARCHIVES:
        oldest = files.pop(0)
        oldest.unlink()


def main():
    workspace = Path(os.environ.get("ARF_HOOK_WORKSPACE", "."))
    session_id = os.environ.get("ARF_HOOK_SESSION_ID", "")
    session_title = os.environ.get("ARF_HOOK_SESSION_TITLE", "新会话")

    # Read stdin
    stdin_raw = sys.stdin.read()
    try:
        input_data = json.loads(stdin_raw) if stdin_raw.strip() else {}
    except json.JSONDecodeError:
        input_data = {}

    data = input_data.get("data", {})
    conversation = data.get("conversation", [])
    session_start_str = data.get("session_start", "")

    if not conversation or len(conversation) < 2:
        print(json.dumps({"archived": False, "reason": "too few messages"}))
        sys.exit(0)

    # Derive session_id from session_start if not provided
    if not session_id and session_start_str:
        try:
            start_time = datetime.fromisoformat(session_start_str)
            session_id = start_time.strftime("%Y%m%d_%H%M%S")
        except (ValueError, TypeError):
            pass

    if not session_id:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    sessions_dir = workspace / "memory" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    start_time = session_start_str or now.isoformat()
    try:
        created_at = datetime.fromisoformat(start_time).isoformat()
    except (ValueError, TypeError):
        created_at = now.isoformat()

    archive = {
        "id": session_id,
        "title": session_title,
        "created_at": created_at,
        "ended_at": now.isoformat(),
        "message_count": len(conversation),
        "messages": conversation,
    }

    path = sessions_dir / f"{session_id}.json"
    path.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _evict_oldest(sessions_dir)

    print(json.dumps(
        {"archived": True, "session_id": session_id},
        ensure_ascii=False,
    ))
    sys.exit(0)


if __name__ == "__main__":
    main()
