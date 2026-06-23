"""PeerTeamState — typed runtime state owned by PeerTeamPlugin.

Each plugin instance keeps its own ``PeerTeamState`` with per-agent data
(agent_bus, peer_harnesses, entry_points, context_injected_sessions,
_wait_tasks).  Cross-agent shared state (bus registry, pending_replies,
last_activity) lives at module level — no global singleton.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class PeerTeamState:
    """Per-agent runtime state, owned by a single PeerTeamPlugin instance."""

    agent_bus: object | None = None
    peer_harnesses: dict[str, object] = field(default_factory=dict)
    context_injected_sessions: set[str] = field(default_factory=set)
    entry_points: dict[str, bool] = field(default_factory=dict)
    data_dir: str = "./data"
    _wait_tasks: dict[str, "asyncio.Task[None]"] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
# Module-level shared state — cross-agent, not owned by any single plugin.
# ═══════════════════════════════════════════════════════════════════════

_bus_registry: dict[str, object] = {}
_pending_replies: dict[str, dict] = {}
_last_activity: dict[str, float] = {}


# ── Bus registry ──────────────────────────────────────────────────────

def register_bus(sid: str, bus: object) -> None:
    """Register an agent's bus so peers can look it up by session_id."""
    _bus_registry[sid] = bus


def unregister_bus(sid: str) -> None:
    """Remove an agent's bus from the registry."""
    _bus_registry.pop(sid, None)


def get_bus(sid: str) -> object | None:
    """Return the bus for *sid*, or None."""
    return _bus_registry.get(sid)


def get_registered_sids() -> list[str]:
    """Return all registered session_ids."""
    return list(_bus_registry.keys())


# ── Pending replies ───────────────────────────────────────────────────

def get_pending_replies() -> dict[str, dict]:
    """Return the shared pending_replies dict."""
    return _pending_replies


def get_last_activity() -> dict[str, float]:
    """Return the shared last_activity dict."""
    return _last_activity


# ── Persistence ───────────────────────────────────────────────────────


async def save_pending_replies(data_dir: str = "./data") -> None:
    """Persist shared pending_replies to disk."""
    import json as _json
    from pathlib import Path

    path = Path(data_dir) / "pending_replies.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        _json.dumps(_pending_replies, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


async def restore_pending_replies(data_dir: str = "./data") -> None:
    """Restore shared pending_replies from disk if in-memory is empty."""
    import json as _json
    from pathlib import Path

    if _pending_replies:
        return
    path = Path(data_dir) / "pending_replies.json"
    if not path.exists():
        return
    try:
        loaded = _json.loads(path.read_text(encoding="utf-8"))
        _pending_replies.update(loaded)
    except (OSError, _json.JSONDecodeError):
        pass
