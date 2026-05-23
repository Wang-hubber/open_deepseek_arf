from pathlib import Path
from arf.core.results import GuardResult


class PathCheckToolGuard:
    def __init__(self, workspace_root: str = "") -> None:
        self._root = workspace_root

    async def check(self, tool_name: str, params: dict) -> GuardResult:
        for v in params.values():
            if isinstance(v, str) and ".." in Path(v).parts:
                return GuardResult(allowed=False, reason=f"Path traversal detected in '{v}'")
            if isinstance(v, str) and v.startswith("/"):
                return GuardResult(allowed=False, reason=f"Absolute path denied: '{v}'")
        return GuardResult(allowed=True)
