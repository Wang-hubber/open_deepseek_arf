"""Session archive management.

Archives ended sessions as JSON files in
workspace/memory/sessions/<session_id>/archive.json.
Users manage session lifecycle via the frontend delete button.
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "新会话"


def _archive_path(workspace_dir: str | Path, session_id: str) -> Path:
    return Path(workspace_dir) / "memory" / "sessions" / session_id / "archive.json"


def list_archives(workspace_dir: str | Path) -> list[dict]:
    """Return metadata for all archived sessions, newest first, without messages."""
    sessions_dir = Path(workspace_dir) / "memory" / "sessions"
    if not sessions_dir.exists():
        return []

    results = []
    for p in sorted(sessions_dir.glob("*/archive.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            results.append({
                "id": data["id"],
                "title": data.get("title", DEFAULT_TITLE),
                "created_at": data["created_at"],
                "ended_at": data["ended_at"],
                "message_count": data["message_count"],
            })
        except (json.JSONDecodeError, KeyError):
            logger.warning("Skipping corrupt session archive: %s", p)
    return results


def get_archive(session_id: str, workspace_dir: str | Path) -> dict | None:
    """Return the full archive (with messages) for a given session id."""
    path = _archive_path(workspace_dir, session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return None


def update_title(session_id: str, title: str, workspace_dir: str | Path) -> bool:
    """Update the title of an archived session. Returns True on success."""
    path = _archive_path(workspace_dir, session_id)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["title"] = title
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except (json.JSONDecodeError, KeyError):
        return False


def delete_archive(session_id: str, workspace_dir: str | Path) -> bool:
    """Delete an archived session directory. Returns True if deleted, False if not found."""
    session_dir = Path(workspace_dir) / "memory" / "sessions" / session_id
    if not session_dir.exists():
        return False
    shutil.rmtree(session_dir)
    logger.info("Session archive deleted: %s", session_id)
    return True
