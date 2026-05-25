"""Restore agent state on startup — FileStateStore handles persistence automatically.

Every engine turn triggers state_store.put(), which writes to
memory/sessions/<session_id>.json. No manual save needed.
"""
from pathlib import Path


def load_archive(workspace: str | Path = "./memory") -> dict | None:
    """Read archived state from FileStateStore (backward compat with archive.json)."""
    archive = Path(workspace) / "archive.json"
    if archive.exists():
        import json
        try:
            return json.loads(archive.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


async def save_archive_async(agent) -> None:
    """No-op — FileStateStore persists state automatically on every turn."""
    pass
