"""RoundManager — round-level checkpoint and undo."""
import copy
import json
import logging
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from arf.core.state import AgentState

logger = logging.getLogger("arf.engine.rounds")


@dataclass
class RoundTransaction:
    """Full state snapshot for one user interaction round.

    Captures state at the beginning of the round. Undo restores to this point.
    """

    round_id: str                       # "session_id/round_num"
    round_num: int                      # monotonic, lifetime of RoundManager
    state_snapshot: dict                # deepcopy(AgentState) at round start
    workspace_snapshot_dir: str | None = None  # data/checkpoints/{round_num}/
    created_at: float = field(default_factory=time.time)
    agent_trace: list[str] = field(default_factory=list)  # agent names visited
    closed: bool = False


class RoundManager:
    """Round-level checkpoint manager.

    Each round is a transaction: begin_round() pushes a snapshot.
    undo(N) restores to round-N ago.
    """

    _PERSIST_FILE = Path("data/checkpoints/rounds.json")

    def __init__(self, max_undo_depth: int = 3) -> None:
        self._max_depth = max_undo_depth
        self._rounds: deque[RoundTransaction] = deque(maxlen=max_undo_depth)
        self._active: RoundTransaction | None = None
        self._current_round: int = 0
        self._restore_from_disk()

    # -- public API --

    def begin_round(self, state: AgentState, workspace_dir: str = "") -> RoundTransaction:
        """Snapshot *state* and workspace files.  Returns the new transaction."""
        self._current_round += 1
        agent = state.get("agent_name", "main")
        snapshot = copy.deepcopy(dict(state))
        tx = RoundTransaction(
            round_id=f"{state.get('session_id', 'default')}/{self._current_round}",
            round_num=self._current_round,
            state_snapshot=snapshot,
            agent_trace=[agent],
        )
        ws = Path(workspace_dir) if workspace_dir else Path("workspaces/default")
        tx.workspace_snapshot_dir = self._snapshot_workspace(
            ws, self._current_round, state_snapshot=snapshot
        )

        self._rounds.append(tx)
        self._active = tx
        self._save_rounds()
        return tx

    def close_round(self) -> None:
        """Mark the active round as complete."""
        if self._active:
            self._active.closed = True
            self._active = None
            self._save_rounds()

    def undo(self, steps: int, workspace_dir: str = "") -> AgentState | None:
        """Pop N rounds and restore state from the oldest popped.

        Returns the state snapshot from the target (restored) round,
        or None if insufficient rounds.
        """
        if steps < 1 or steps > len(self._rounds):
            return None

        target = None
        for _ in range(steps):
            target = self._rounds.pop()

        if target is None:
            return None

        ws = Path(workspace_dir) if workspace_dir else Path("workspaces/default")
        self._restore_workspace_files(target, ws)
        self._cleanup_checkpoint_dirs(target.round_num, ws)
        self._active = None
        self._save_rounds()

        return copy.deepcopy(target.state_snapshot)

    def count(self) -> int:
        return len(self._rounds)

    @property
    def active_round(self) -> RoundTransaction | None:
        return self._active

    @property
    def current_round_num(self) -> int:
        return self._current_round

    # -- internal --

    # Directories and file patterns excluded from workspace snapshots.
    # .venv, __pycache__, node_modules etc. can contain 10k+ files and
    # are never user-modifiable workspace content.
    _SNAPSHOT_EXCLUDE = {".git", ".venv", "__pycache__", "node_modules",
                         ".mypy_cache", ".pytest_cache", "*.pyc", "*.pyo",
                         "data"}

    def _snapshot_workspace(self, workspace: Path, round_num: int,
                            state_snapshot: dict | None = None) -> str | None:
        """Copy workspace files to data/checkpoints/{round_num}/.

        If *state_snapshot* is provided, also writes state.json into the
        checkpoint directory for crash-safe undo recovery.

        Excludes large generated directories (_SNAPSHOT_EXCLUDE) so
        snapshotting the project root doesn't copy .venv or framework code.
        """
        ckpt_dir = Path("data/checkpoints") / str(round_num)
        if ckpt_dir.exists():
            shutil.rmtree(ckpt_dir)
        ckpt_dir.mkdir(parents=True, exist_ok=True)

        if workspace.exists():
            for f in workspace.rglob("*"):
                if not f.is_file():
                    continue
                parts = set(f.parts)
                if parts & self._SNAPSHOT_EXCLUDE:
                    continue
                if any(f.match(p) for p in self._SNAPSHOT_EXCLUDE if p.startswith("*")):
                    continue
                rel = f.relative_to(workspace)
                dest = ckpt_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

        if state_snapshot is not None:
            (ckpt_dir / "state.json").write_text(
                json.dumps(state_snapshot, ensure_ascii=False), encoding="utf-8"
            )

        return str(ckpt_dir) if (workspace.exists() or state_snapshot is not None) else None

    def _restore_workspace_files(self, tx: RoundTransaction, workspace: Path) -> None:
        """Delete current workspace files and restore from *tx* snapshot."""
        if not tx.workspace_snapshot_dir or not workspace.exists():
            return
        ckpt = Path(tx.workspace_snapshot_dir)
        if not ckpt.exists():
            return
        # Remove current files (non-git)
        for f in workspace.rglob("*"):
            if f.is_file() and ".git" not in f.parts:
                f.unlink()
        # Restore from checkpoint
        for f in ckpt.rglob("*"):
            if f.is_file():
                rel = f.relative_to(ckpt)
                dest = workspace / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

    def _cleanup_checkpoint_dirs(self, from_round: int, workspace: Path) -> None:
        """Remove checkpoint directories >= *from_round*."""
        ckpts = Path("data/checkpoints")
        if not ckpts.exists():
            return
        for d in ckpts.iterdir():
            if d.is_dir():
                try:
                    if int(d.name) >= from_round:
                        shutil.rmtree(d)
                except (ValueError, OSError):
                    pass

    # -- persistence --

    def _persist_file(self) -> Path:
        return self._PERSIST_FILE

    def _save_rounds(self) -> None:
        """Persist round metadata (not full state snapshots) to disk."""
        try:
            self._persist_file().parent.mkdir(parents=True, exist_ok=True)
            data = []
            for tx in self._rounds:
                data.append({
                    "round_id": tx.round_id,
                    "round_num": tx.round_num,
                    "agent_trace": tx.agent_trace,
                    "created_at": tx.created_at,
                    "workspace_snapshot_dir": tx.workspace_snapshot_dir,
                    "closed": tx.closed,
                })
            self._persist_file().write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            logger.warning("Failed to persist rounds: %s", e)

    def _restore_from_disk(self) -> None:
        """Rebuild RoundTransaction deque from persisted checkpoint data.

        Each checkpoint dir contains state.json (full state snapshot) and
        workspace files.  rounds.json indexes them.  On startup this
        reconstructs the rolling window so undo works immediately.
        """
        pf = self._persist_file()
        if not pf.exists():
            return
        try:
            index = json.loads(pf.read_text(encoding="utf-8"))
            if not isinstance(index, list):
                return
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read rounds index: %s", e)
            return

        restored = 0
        for entry in index[-self._max_depth:]:
            round_num = entry.get("round_num", 0)
            ckpt_dir = Path("data/checkpoints") / str(round_num)
            state_file = ckpt_dir / "state.json"
            if not state_file.exists():
                continue
            try:
                state_snapshot = json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to restore round %d state: %s", round_num, e)
                continue

            tx = RoundTransaction(
                round_id=entry.get("round_id", f"default/{round_num}"),
                round_num=round_num,
                state_snapshot=state_snapshot,
                workspace_snapshot_dir=entry.get("workspace_snapshot_dir"),
                created_at=entry.get("created_at", time.time()),
                agent_trace=entry.get("agent_trace", []),
                closed=entry.get("closed", False),
            )
            self._rounds.append(tx)
            self._current_round = max(self._current_round, round_num)
            restored += 1

        if restored:
            logger.info("Restored %d round(s) from disk (current_round=%d, undo available)",
                        restored, self._current_round)
