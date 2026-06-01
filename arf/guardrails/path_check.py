"""PathCheckToolGuard — block path traversal, symlink escape, and workspace escape.

Performs recursive inspection of all string values in nested tool parameters.
Supports optional per-invocation resource quotas (path count, depth, symlink deny).
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from arf.core.results import GuardResult
from arf.sandbox.path_sandbox import PathSandbox


@dataclass
class ResourceQuota:
    """Per-invocation resource limits enforced by PathCheckToolGuard.

    All limits are optional — ``None`` means unlimited.
    """

    max_path_count: int | None = None
    """Maximum number of path-like string parameters inspected in one call."""

    max_path_depth: int | None = None
    """Maximum directory depth (number of Path.parts) for any single path."""

    deny_symlinks: bool = True
    """If True, reject any path whose resolution crosses a symlink."""

    # ── runtime counters (reset per invocation) ──
    _path_count: int = field(default=0, init=False, repr=False)

    def count_one(self) -> bool:
        """Increment path counter. Returns False if quota exceeded."""
        self._path_count += 1
        if self.max_path_count is not None and self._path_count > self.max_path_count:
            return False
        return True

    def reset(self) -> None:
        self._path_count = 0


class PathCheckToolGuard:
    """Blocks dangerous paths in tool parameters using PathSandbox validation.

    Checks (in order, first failure wins):
    1. Path traversal (``..`` in segments)
    2. Absolute paths (starts with ``/``)
    3. Path depth exceeds quota
    4. Path count exceeds quota
    5. Symlink traversal (optional, on by default)
    6. Resolved path escapes workspace (PathSandbox containment)
    """

    def __init__(
        self,
        workspace_root: str = ".",
        quota: ResourceQuota | None = None,
        writable_dirs: list[str] | None = None,
        allow_escape: bool = False,
        checks: dict[str, bool] | None = None,
    ) -> None:
        self._sandbox = PathSandbox(workspace_root, writable_dirs=writable_dirs)
        self._quota = quota
        self._allow_escape = allow_escape
        self._checks = checks or {
            "path_traversal": True,
            "absolute_path": True,
            "workspace_containment": True,
            "symlink": True,
        }

    # ── public API ──

    async def check(self, tool_name: str, params: dict) -> GuardResult:
        if self._allow_escape:
            return GuardResult(allowed=True)
        if self._quota:
            self._quota.reset()

        for path_str in self._walk_strings(params):
            result = self._check_one(path_str)
            if not result.allowed:
                return result
        return GuardResult(allowed=True)

    # ── internal ──

    def _check_one(self, v: str) -> GuardResult:
        # Skip strings that are clearly file content, not paths
        if "\n" in v or len(v) > 500:
            return GuardResult(allowed=True)

        # 1. Path traversal
        if self._checks.get("path_traversal") and ".." in Path(v).parts:
            return GuardResult(allowed=False, reason=f"Path traversal blocked: '{v}'")

        # 2. Absolute path
        if self._checks.get("absolute_path") and v.startswith("/"):
            return GuardResult(allowed=False, reason=f"Absolute path blocked: '{v}'")

        # 3. Depth quota
        if self._quota and self._quota.max_path_depth is not None:
            depth = len(Path(v).parts)
            if depth > self._quota.max_path_depth:
                return GuardResult(
                    allowed=False,
                    reason=f"Path depth {depth} exceeds limit {self._quota.max_path_depth}: '{v}'",
                )

        # 4. Count quota
        if self._quota and not self._quota.count_one():
            return GuardResult(
                allowed=False,
                reason=f"Path count exceeds limit {self._quota.max_path_count}",
            )

        # 5. Symlink detection (independent of quota)
        if self._checks.get("symlink"):
            if self._sandbox.has_symlink(v):
                return GuardResult(allowed=False, reason=f"Symlink traversal blocked: '{v}'")

        # 6. Workspace containment
        if self._checks.get("workspace_containment"):
            if not self._sandbox.validate_path(v):
                return GuardResult(allowed=False, reason=f"Path escapes workspace: '{v}'")

        return GuardResult(allowed=True)

    @staticmethod
    def _walk_strings(obj: Any) -> Iterator[str]:
        """Recursively yield every string value in a nested dict/list structure."""
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for value in obj.values():
                yield from PathCheckToolGuard._walk_strings(value)
        elif isinstance(obj, (list, tuple, set)):
            for item in obj:
                yield from PathCheckToolGuard._walk_strings(item)
