"""PathSandbox — path resolution utility for DirectoryBoundary."""
from pathlib import Path


class PathSandbox:
    """Lightweight path resolver. Boundary logic lives in DirectoryBoundary."""

    def __init__(self, workspace_root: str | Path = ".") -> None:
        self._root = Path(workspace_root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def resolve_path(self, path_str: str, workspace_root: str = "") -> Path:
        root = Path(workspace_root or self._root).resolve()
        return (root / path_str).resolve()

    def validate_command(self, command: str) -> bool:
        dangerous = [";", "&&", "|", "$(", "`", "rm -rf /", "sudo"]
        return not any(d in command for d in dangerous)
