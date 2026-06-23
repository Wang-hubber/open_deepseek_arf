"""PeerTeamState — typed runtime state owned by PeerTeamPlugin."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class PeerTeamState:
    """Runtime state for a peer team, owned and managed by PeerTeamPlugin.

    Tools and the background wait loop access this via ``get_state()``.
    """

    agent_bus: object | None = None
    peer_harnesses: dict[str, object] = field(default_factory=dict)
    pending_replies: dict[str, dict[str, str]] = field(default_factory=dict)
    context_injected_sessions: set[str] = field(default_factory=set)
    entry_points: dict[str, bool] = field(default_factory=dict)
    last_activity: dict[str, float] = field(default_factory=dict)
    data_dir: str = "./data"
    _wait_tasks: dict[str, "asyncio.Task[None]"] = field(default_factory=dict)


_state: PeerTeamState | None = None


def get_state() -> PeerTeamState:
    """Return the current PeerTeamState, set by PeerTeamPlugin at init."""
    if _state is None:
        raise RuntimeError(
            "PeerTeamState not initialized — is the a2a_teammates plugin enabled?"
        )
    return _state


async def save_pending_replies() -> None:
    """Persist pending_replies to disk after mutation."""
    import json as _json
    from pathlib import Path
    s = get_state()
    path = Path(s.data_dir) / "pending_replies.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        _json.dumps(s.pending_replies, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


async def restore_pending_replies() -> None:
    """Restore pending_replies from disk if in-memory is empty."""
    import json as _json
    from pathlib import Path
    s = get_state()
    if s.pending_replies:
        return
    path = Path(s.data_dir) / "pending_replies.json"
    if not path.exists():
        return
    try:
        s.pending_replies = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        pass
