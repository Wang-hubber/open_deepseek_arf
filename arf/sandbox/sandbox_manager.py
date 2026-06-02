"""SandboxManager — session-level sandbox isolation for tool execution."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class FileChange:
    """A single file change detected by sandbox diff."""
    path: str
    type: Literal["added", "modified", "deleted"]


@dataclass
class SandboxDiff:
    """Result of comparing sandbox vs workspace."""
    added: list[FileChange] = field(default_factory=list)
    modified: list[FileChange] = field(default_factory=list)
    deleted: list[FileChange] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)


class SandboxManager:
    """Session-level sandbox isolation manager.

    Lifecycle:
        init_session() → tools run in sandbox → diff() → persist() → destroy()
    """

    def __init__(
        self,
        workspace_root: str | Path,
        blacklist: list[str] | None = None,
        auto_destroy: bool = False,
    ) -> None:
        self._workspace = Path(workspace_root).resolve()
        self._sandbox_root = self._workspace / "sandbox"
        self._blacklist = blacklist or [".git", "__pycache__", "logs", ".env"]
        self._auto_destroy = auto_destroy
        self._persisted: dict[str, set[str]] = {}

    @property
    def workspace_root(self) -> Path:
        return self._workspace

    def sandbox_path(self, session_id: str) -> Path:
        return self._sandbox_root / session_id

    def init_session(self, session_id: str) -> Path:
        """Copy workspace → sandbox/{session_id}, excluding blacklist."""
        dst = self.sandbox_path(session_id)
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True, exist_ok=True)
        self._copy_with_blacklist(self._workspace, dst, set(self._blacklist))
        self._persisted[session_id] = set()
        return dst

    def diff(self, session_id: str) -> SandboxDiff:
        """Compare sandbox vs workspace, return changed files."""
        result = SandboxDiff()
        sandbox = self.sandbox_path(session_id)
        persisted = self._persisted.get(session_id, set())

        for sb_file in sandbox.rglob("*"):
            if sb_file.is_dir():
                continue
            rel = str(sb_file.relative_to(sandbox))
            ws_file = self._workspace / rel
            if not ws_file.exists():
                result.added.append(FileChange(path=rel, type="added"))
            elif sb_file.read_bytes() != ws_file.read_bytes():
                result.modified.append(FileChange(path=rel, type="modified"))

        for ws_file in self._workspace.rglob("*"):
            if ws_file.is_dir():
                continue
            rel = str(ws_file.relative_to(self._workspace))
            sb_file = sandbox / rel
            if not sb_file.exists() and rel not in persisted:
                result.deleted.append(FileChange(path=rel, type="deleted"))

        return result

    def persist(self, session_id: str, approved_paths: list[str]) -> None:
        """Copy approved files from sandbox back to workspace."""
        sandbox = self.sandbox_path(session_id)
        persisted = self._persisted.get(session_id, set())
        for rel in approved_paths:
            src = sandbox / rel
            dst = self._workspace / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                persisted.add(rel)
        self._persisted[session_id] = persisted

    def destroy(self, session_id: str) -> None:
        """Remove sandbox/{session_id}."""
        path = self.sandbox_path(session_id)
        if path.exists():
            shutil.rmtree(path)
        self._persisted.pop(session_id, None)

    def pending_changes(self, session_id: str) -> list[FileChange]:
        """List unpersisted changes in sandbox."""
        diff = self.diff(session_id)
        persisted = self._persisted.get(session_id, set())
        result = []
        for c in diff.added + diff.modified:
            if c.path not in persisted:
                result.append(c)
        return result

    @property
    def auto_destroy(self) -> bool:
        return self._auto_destroy

    def _copy_with_blacklist(self, src: Path, dst: Path, blacklist: set[str]) -> None:
        for item in src.iterdir():
            if item.name in blacklist:
                continue
            if item.name == "sandbox":
                continue
            target = dst / item.name
            if item.is_dir():
                target.mkdir(exist_ok=True)
                self._copy_with_blacklist(item, target, blacklist)
            else:
                shutil.copy2(item, target)
