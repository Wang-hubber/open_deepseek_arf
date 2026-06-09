"""PathCheckToolGuard — block path traversal, symlink escape, and workspace escape.

Performs recursive inspection of all string values in nested tool parameters.
Boundary is passed per-call by the executor (not stored internally).
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from arf.core.results import GuardResult
from arf.sandbox.directory_boundary import DirectoryBoundary


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

    Checks (in order, first failure wins):
    1. Path traversal (``..`` in segments)
    2. Absolute paths (starts with ``/``)
    3. Path depth exceeds quota
    4. Path count exceeds quota
    5. Symlink traversal
    6. Boundary containment (whitelist)
    """

    def __init__(
        self,
        quota: ResourceQuota | None = None,
        checks: dict[str, bool] | None = None,
    ) -> None:
        self._quota = quota
        self._checks = checks or {
            "path_traversal": True,
            "absolute_path": True,
            "workspace_containment": True,
            "symlink": True,
        }

    async def check(
        self, tool_name: str, params: dict, boundary: DirectoryBoundary
    ) -> GuardResult:
        """Validate all string params against *boundary*.

        boundary is provided by the executor, resolved per-tool.
        """
        if self._quota:
            self._quota.reset()

        for path_str in self._walk_strings(params):
            result = self._check_one(path_str, boundary)
            if not result.allowed:
                return result
        return GuardResult(allowed=True)

    _MAX_PATH_LEN = 255

    def _check_one(self, v: str, boundary: DirectoryBoundary) -> GuardResult:
        # ── Sanitize ──
        cleaned = v.rstrip()  # drop trailing whitespace / newlines

        # ── Reject: impossible to be a path ──
        if "\x00" in cleaned:
            return GuardResult(allowed=False, reason="Path contains null byte")

        if len(cleaned) > self._MAX_PATH_LEN:
            return GuardResult(
                allowed=False,
                reason=f"Path too long ({len(cleaned)} > {self._MAX_PATH_LEN})",
            )

        # ── Allow: clearly file content, not a path ──
        if "\n" in cleaned:
            return GuardResult(allowed=True)

        # ── Path checks ──
        parts = Path(cleaned).parts

        if self._checks.get("path_traversal") and ".." in parts:
            return GuardResult(allowed=False, reason=f"Path traversal blocked: '{v}'")

        if self._checks.get("absolute_path") and cleaned.startswith("/"):
            return GuardResult(allowed=False, reason=f"Absolute path blocked: '{v}'")

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
    def _walk_strings(obj: Any) -> Iterator[str]:
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for value in obj.values():
                yield from PathCheckToolGuard._walk_strings(value)
        elif isinstance(obj, (list, tuple, set)):
            for item in obj:
                yield from PathCheckToolGuard._walk_strings(item)
