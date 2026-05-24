"""PathCheckToolGuard — block path traversal and workspace escape."""
from pathlib import Path
from arf.core.results import GuardResult
from arf.sandbox.path_sandbox import PathSandbox


class PathCheckToolGuard:
    """Blocks dangerous paths in tool parameters using PathSandbox validation.

    Checks:
    - Path traversal (..)
    - Absolute paths (/)
    - Resolved path escapes workspace (via PathSandbox.is_relative_to)
    """

    def __init__(self, workspace_root: str = ".") -> None:
        self._sandbox = PathSandbox(workspace_root)

    async def check(self, tool_name: str, params: dict) -> GuardResult:
        for v in params.values():
            if not isinstance(v, str):
                continue
            # Quick reject: path traversal
            if ".." in Path(v).parts:
                return GuardResult(allowed=False, reason=f"Path traversal blocked: '{v}'")
            # Quick reject: absolute path
            if v.startswith("/"):
                return GuardResult(allowed=False, reason=f"Absolute path blocked: '{v}'")
            # Resolve and check workspace containment
            if not self._sandbox.validate_path(v):
                return GuardResult(allowed=False, reason=f"Path escapes workspace: '{v}'")
        return GuardResult(allowed=True)
