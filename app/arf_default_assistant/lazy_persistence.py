"""Restore agent state on startup — FileStateStore handles persistence automatically.

Every engine turn triggers state_store.put(), which writes to
memory/state/<session_id>.json. No manual save needed.

load_archive() is kept for backward-compat migration of old archive.json data.
"""
from pathlib import Path


def load_archive(workspace: str | Path = "./memory") -> dict | None:
    """Read legacy archive.json as migration fallback."""
    archive = Path(workspace) / "archive.json"
    if archive.exists():
        import json
        try:
            return json.loads(archive.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None
