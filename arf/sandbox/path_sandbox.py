"""PathSandbox — prevent path traversal and workspace escape."""
from pathlib import Path


class PathSandbox:
    def __init__(self, workspace_root: str | Path = ".", writable_dirs: list[str] | None = None) -> None:
        self._root = Path(workspace_root).resolve()
        self._writable = [self._root / d for d in (writable_dirs or [])]

    def validate_path(self, path_str: str, workspace_root: str = "") -> bool:
        root = Path(workspace_root or self._root).resolve()
        resolved = (root / path_str).resolve()
        return resolved.is_relative_to(root) and ".." not in Path(path_str).parts

    def validate_command(self, command: str) -> bool:
        dangerous = [";", "&&", "|", "$(", "`", "rm -rf /", "sudo"]
        return not any(d in command for d in dangerous)

    def allowed_dirs(self) -> list[str]:
        return [str(d) for d in self._writable]
