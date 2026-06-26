"""PathCheckToolGuard — block path traversal, symlink escape, and workspace escape.

Performs recursive inspection of all string values in nested tool parameters.
Boundary is passed per-call by the executor (not stored internally).
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from arf.core.results import GuardResult
from arf.guardrails.directory_boundary import DirectoryBoundary


@dataclass
class ResourceQuota:
    """Per-invocation resource limits enforced by PathCheckToolGuard.

    All limits are optional — ``None`` means unlimited.
    """

    max_path_count: int | None = None
    max_path_depth: int | None = None
    deny_symlinks: bool = True

    _path_count: int = field(default=0, init=False, repr=False)

    def count_one(self) -> bool:
        self._path_count += 1
        if self.max_path_count is not None and self._path_count > self.max_path_count:
            return False
        return True

    def reset(self) -> None:
        self._path_count = 0


class PathCheckToolGuard:
    """Blocks dangerous paths in tool parameters using DirectoryBoundary.

    Security model: a path is safe if it (a) contains no ``..`` traversal,
    (b) resolves within the allowed directory boundary, and (c) contains
    no symlink that escapes the boundary.  Absolute paths that satisfy all
    three conditions are permitted — the boundary check is the real guard.

    Checks (in order, first failure wins):
    1. Path traversal (``..`` in segments)
    2. Path depth exceeds quota
    3. Path count exceeds quota
    4. Symlink traversal
    5. Boundary containment (whitelist)
    """

    def __init__(
        self,
        quota: ResourceQuota | None = None,
        checks: dict[str, bool] | None = None,
    ) -> None:
        self._quota = quota
        self._checks = checks or {
            "path_traversal": True,
            "workspace_containment": True,
            "symlink": True,
        }

    async def check(
        self,
        tool_name: str,
        params: dict,
        boundary: DirectoryBoundary,
        path_param_names: set[str] | None = None,
    ) -> GuardResult:
        """Validate path-like params against *boundary*.

        If *path_param_names* is provided, only those top-level keys
        are checked.  Otherwise every string value in *params* is scanned
        (backward-compatible default).
        """
        if self._quota:
            self._quota.reset()

        for path_str in self._walk_strings(params, path_param_names):
            result = self._check_one(path_str, boundary)
            if not result.allowed:
                return result
        return GuardResult(allowed=True)

    _MAX_PATH_LEN = 255

    def _check_one(self, v: str, boundary: DirectoryBoundary) -> GuardResult:
        # ── Reject: cannot be a legitimate path ──
        if "\x00" in v:
            return GuardResult(allowed=False, reason="Path contains null byte")

        if "\n" in v:
            return GuardResult(allowed=False, reason="Path contains newline")

        if len(v) > self._MAX_PATH_LEN:
            return GuardResult(
                allowed=False,
                reason=f"Path too long ({len(v)} > {self._MAX_PATH_LEN})",
            )

        cleaned = v.rstrip()

        # ── Path checks ──
        parts = Path(cleaned).parts

        if self._checks.get("path_traversal") and ".." in parts:
            return GuardResult(allowed=False, reason=f"Path traversal blocked: '{v}'")

        if self._quota and self._quota.max_path_depth is not None:
            depth = len(parts)
            if depth > self._quota.max_path_depth:
                return GuardResult(
                    allowed=False,
                    reason=f"Path depth {depth} exceeds limit {self._quota.max_path_depth}: '{v}'",
                )

        if self._quota and not self._quota.count_one():
            return GuardResult(
                allowed=False,
                reason=f"Path count exceeds limit {self._quota.max_path_count}",
            )

        if self._checks.get("symlink"):
            if boundary.has_symlink(cleaned):
                return GuardResult(allowed=False, reason=f"Symlink traversal blocked: '{v}'")

        if self._checks.get("workspace_containment"):
            if not boundary.contains(cleaned):
                return GuardResult(allowed=False, reason=f"Path outside allowed directory: '{v}'")

        return GuardResult(allowed=True)

    @staticmethod
    def _walk_strings(obj: Any,
                      path_param_names: set[str] | None = None) -> Iterator[str]:
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for key, value in obj.items():
                if path_param_names is not None and key not in path_param_names:
                    continue
                yield from PathCheckToolGuard._walk_strings(
                    value, path_param_names)
        elif isinstance(obj, (list, tuple, set)):
            for item in obj:
                yield from PathCheckToolGuard._walk_strings(
                    item, path_param_names)
