"""StateStore implementations — in-memory and file-backed."""
import json
import logging
from pathlib import Path
from arf.core.state import AgentState

logger = logging.getLogger("arf.engine.checkpoint")


class InMemoryStateStore:
    """Dict-backed store. Fast but lost on process restart."""

    def __init__(self) -> None:
        self._store: dict[str, AgentState] = {}
        self.snapshots: list[dict] = []   # for testing

    async def put(self, session_id: str, state: AgentState) -> None:
        import copy
        snapshot = copy.deepcopy(dict(state))
        self._store[session_id] = snapshot
        self.snapshots.append({"session_id": session_id, "turn": snapshot.get("current_turn", 0)})

    async def get(self, session_id: str) -> AgentState | None:
        return self._store.get(session_id)

    async def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    async def list_sessions(self) -> list[str]:
        return list(self._store.keys())

    def reset(self) -> None:
        self._store.clear()
        self.snapshots.clear()


class FileStateStore:
    """JSON-file-backed store. Survives process restarts.

    Writes state to ``<data_dir>/<session_id>/state/<session_id>.json`` on every put().
    """

    def __init__(self, data_dir: str | Path = "./data") -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        p = self._dir / session_id / "state"
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{session_id}.json"

    async def put(self, session_id: str, state: AgentState) -> None:
        import copy
        path = self._path(session_id)
        tmp = path.with_suffix(".tmp")
        data = copy.deepcopy(dict(state))
        # Don't persist ephemeral tool_results across restarts
        data.pop("tool_results", None)
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    async def get(self, session_id: str) -> AgentState | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Corrupted state file %s: %s", path, e)
            return None

    async def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    async def list_sessions(self) -> list[str]:
        sessions = []
        for d in self._dir.iterdir():
            if d.is_dir() and (d / "state").exists():
                sessions.append(d.name)
        return sorted(sessions)
