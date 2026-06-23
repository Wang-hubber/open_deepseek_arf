"""A2A Teammates plugin tools — registry singleton."""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("arf.plugins.a2a_teammates.tools")


class _TeammatesRegistry:
    """Module-level singleton bridging plugin and tool functions.

    Replaces old ParkCoordinator with harness-native park/resume.
    Each agent's _peer_wait_loop holds its OWN harness reference and
    wakes itself via resolve_wait() — no cross-harness coordination.
    """

    def __init__(self) -> None:
        self.agent_bus: object | None = None
        # role_key → harness ref (captured at init)
        self._peer_harnesses: dict[str, object] = {}
        # role_key → wait_id (parked agent's wait)
        self._peer_wait_ids: dict[str, object] = {}
        # correlation_id → {sender, receiver}
        self._pending_replies: dict[str, dict[str, str]] = {}
        # session_ids that already got team context injected
        self._peer_context_injected: set[str] = set()
        # session_id → is_entry_point
        self._entry_points: dict[str, bool] = {}
        # role_key → monotonic timestamp for receiver liveness
        self._last_activity: dict[str, float] = {}
        # role_key → asyncio.Event to cancel previous wait_loop
        self._peer_cancel_events: dict[str, asyncio.Event] = {}
        # data_dir set by plugin at init
        self.data_dir: str = "./data"


_registry = _TeammatesRegistry()
