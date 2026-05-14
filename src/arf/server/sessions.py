"""Session archive management.

Archives ended sessions as JSON files in workspace/memory/sessions/.
Keeps at most MAX_ARCHIVES sessions, circularly overwriting the oldest.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_ARCHIVES = 10
DEFAULT_TITLE = "新会话"


def archive_session(
    history: list[dict],
    start_time: datetime,
    workspace_dir: str | Path,
    title: str = DEFAULT_TITLE,
    graph_traces: list[dict] | None = None,
    usage: dict | None = None,
) -> str | None:
    """Persist a ended session to disk, evicting the oldest if at capacity.

    Args:
        history: Conversation messages (user/assistant pairs).
        start_time: Session start datetime.
        workspace_dir: User workspace root.
        title: Session title.
        graph_traces: Optional LangGraph node execution traces.
        usage: Optional accumulated token usage across the session.

    Returns the session id on success, None if there's nothing to archive.
    """
    if not history or len(history) < 2:
        return None

    ws = Path(workspace_dir)
    sessions_dir = ws / "memory" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    session_id = start_time.strftime("%Y%m%d_%H%M%S")

    archive = {
        "id": session_id,
        "title": title,
        "created_at": start_time.isoformat(),
        "ended_at": now.isoformat(),
        "message_count": len(history),
        "messages": history,
    }
    if graph_traces:
        archive["graph_traces"] = graph_traces
    if usage:
        archive["usage"] = usage

    path = sessions_dir / f"{session_id}.json"
    path.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
    # Evict AFTER successful write to avoid data loss on failure
    _evict_oldest(sessions_dir)

    # Save session cost summary to DB
    if usage:
        try:
            from ..server.database import save_session_cost
            model_breakdown = {}
            traces = graph_traces or []
            for t in traces:
                m = t.get("model")
                if m:
                    model_breakdown[m] = model_breakdown.get(m, 0) + t.get("total_tokens", 0)
            save_session_cost(
                session_id,
                total_tokens=usage.get("total_tokens", 0),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                model_breakdown=model_breakdown,
            )
        except Exception:
            pass

    logger.info("Session archived: %s (%d messages)", session_id, len(history))
    return session_id


def list_archives(workspace_dir: str | Path) -> list[dict]:
    """Return metadata for all archived sessions, newest first, without messages."""
    sessions_dir = Path(workspace_dir) / "memory" / "sessions"
    if not sessions_dir.exists():
        return []

    results = []
    for p in sorted(sessions_dir.glob("*.json"), reverse=True):
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
            logger.warning("Skipping corrupt session archive: %s", p.name)
    return results


def get_archive(session_id: str, workspace_dir: str | Path) -> dict | None:
    """Return the full archive (with messages) for a given session id."""
    path = Path(workspace_dir) / "memory" / "sessions" / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return None


def update_title(session_id: str, title: str, workspace_dir: str | Path) -> bool:
    """Update the title of an archived session. Returns True on success."""
    path = Path(workspace_dir) / "memory" / "sessions" / f"{session_id}.json"
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
    """Delete an archived session file. Returns True if deleted, False if not found."""
    path = Path(workspace_dir) / "memory" / "sessions" / f"{session_id}.json"
    if not path.exists():
        return False
    path.unlink()
    logger.info("Session archive deleted: %s", session_id)
    return True


def _evict_oldest(sessions_dir: Path):
    """Remove the oldest archive if we're at capacity."""
    files = sorted(sessions_dir.glob("*.json"))
    while len(files) >= MAX_ARCHIVES:
        oldest = files.pop(0)
        oldest.unlink()
        logger.debug("Evicted old session archive: %s", oldest.name)
