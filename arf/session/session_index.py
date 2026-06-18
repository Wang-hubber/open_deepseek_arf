"""SessionIndex — persistent group membership registry for peer teams."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class SessionIndex:
    """Manages the session_index.json file for a peer group.

    File path: ``{data_dir}/{group_id}/session_index.json``

    Member session IDs follow the convention ``{group_id}__{role}``,
    allowing reverse lookup from any member session to its group.
    """

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)

    def _path(self, group_id: str) -> Path:
        return self._data_dir / group_id / "session_index.json"

    @staticmethod
    def parse_session_id(session_id: str) -> tuple[str, str] | None:
        """Parse ``{group_id}__{role}`` → (group_id, role).

        Returns None if the session_id is not a peer member session
        (e.g., a sub-agent session with ``--`` or a plain session).
        """
        # Sub-agent sessions have -- separator — skip those
        if "--" in session_id:
            return None
        parts = session_id.split("__", 1)
        if len(parts) != 2:
            return None
        return parts[0], parts[1]

    async def create(self, group_id: str, members: list[dict]) -> dict:
        """Create a new group index. Returns the full index dict."""
        index = {
            "group_id": group_id,
            "created_at": time.time(),
            "members": members,
        }
        self._path(group_id).parent.mkdir(parents=True, exist_ok=True)
        await self._write(index)
        return index

    async def load(self, group_id: str) -> dict | None:
        """Load a group index from disk. Returns None if not found."""
        path = self._path(group_id)
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8")
            return json.loads(content)
        except (OSError, json.JSONDecodeError):
            return None

    async def update_member(self, group_id: str, role: str, updates: dict) -> None:
        """Update fields on a specific member entry."""
        index = await self.load(group_id)
        if index is None:
            return
        for m in index["members"]:
            if m["role"] == role:
                m.update(updates)
                break
        await self._write(index)

    async def add_child_task(self, group_id: str, role: str, task: dict) -> None:
        """Append a child_tasks entry to a member."""
        index = await self.load(group_id)
        if index is None:
            return
        for m in index["members"]:
            if m["role"] == role:
                m.setdefault("child_tasks", []).append(task)
                break
        await self._write(index)

    async def update_child_status(
        self, group_id: str, role: str,
        child_session_id: str, status: str,
    ) -> None:
        """Update a child_tasks entry's status."""
        index = await self.load(group_id)
        if index is None:
            return
        for m in index["members"]:
            if m["role"] != role:
                continue
            for ct in m.get("child_tasks", []):
                if ct.get("child_session_id") == child_session_id:
                    ct["status"] = status
                    break
        await self._write(index)

    async def _write(self, index: dict) -> None:
        """Atomic write via temp file + rename."""
        path = self._path(index["group_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
