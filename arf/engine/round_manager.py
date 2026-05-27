"""RoundManager — round-level checkpoint and undo for multi-agent scenarios."""
import copy
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from arf.core.state import AgentState


@dataclass
class RoundTransaction:
    """Full state snapshot for one user interaction round.

    A round may span multiple agent handoffs. This snapshot captures
    the state at the beginning of the round. Undo restores to this point.
    """

    round_id: str                       # "session_id/round_num"
    round_num: int                      # monotonic, lifetime of RoundManager
    state_snapshot: dict                # deepcopy(AgentState) at round start
    workspace_snapshot_dir: str | None = None  # memory/checkpoints/{round_num}/
    created_at: float = field(default_factory=time.time)
    agent_trace: list[str] = field(default_factory=list)  # ["main","sys","main"]
    handoff_count: int = 0
    closed: bool = False


class RoundManager:
    """Round-level checkpoint manager.

    Each round is a transaction: begin_round() pushes a snapshot;
    handoffs within the round are recorded via record_handoff() but
    do NOT create new checkpoints.  undo(N) restores to round-N ago.
    """

    def __init__(self, max_undo_depth: int = 3) -> None:
        self._rounds: deque[RoundTransaction] = deque(maxlen=max_undo_depth)
        self._active: RoundTransaction | None = None
        self._current_round: int = 0

    # -- public API --

    def begin_round(self, state: AgentState, workspace_dir: str = "") -> RoundTransaction:
        """Snapshot *state* and workspace files.  Returns the new transaction."""
        self._current_round += 1
        agent = state.get("active_agent") or state.get("agent_name", "main")
        tx = RoundTransaction(
            round_id=f"{state.get('session_id', 'default')}/{self._current_round}",
            round_num=self._current_round,
            state_snapshot=copy.deepcopy(dict(state)),
            agent_trace=[agent],
        )
        ws = Path(workspace_dir) if workspace_dir else Path("workspaces/default")
        tx.workspace_snapshot_dir = self._snapshot_workspace(ws, self._current_round)

        self._rounds.append(tx)
        self._active = tx
        return tx

    def record_handoff(self, from_agent: str, to_agent: str) -> None:
        """Record an agent switch within the active round (no new checkpoint)."""
        if self._active:
            self._active.agent_trace.append(to_agent)
            self._active.handoff_count += 1

    def close_round(self) -> None:
        """Mark the active round as complete (hook for future persistence)."""
        if self._active:
            self._active.closed = True
            self._active = None

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

    def _snapshot_workspace(self, workspace: Path, round_num: int) -> str | None:
        """Copy workspace files to memory/checkpoints/{round_num}/."""
        if not workspace.exists():
            return None
        ckpt_dir = Path("memory/checkpoints") / str(round_num)
        if ckpt_dir.exists():
            shutil.rmtree(ckpt_dir)
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        for f in workspace.rglob("*"):
            if f.is_file() and ".git" not in f.parts:
                rel = f.relative_to(workspace)
                dest = ckpt_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
        return str(ckpt_dir)

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
        ckpts = Path("memory/checkpoints")
        if not ckpts.exists():
            return
        for d in ckpts.iterdir():
            if d.is_dir():
                try:
                    if int(d.name) >= from_round:
                        shutil.rmtree(d)
                except (ValueError, OSError):
                    pass
