"""PathSandbox — prevent path traversal, symlink escape, and workspace escape."""
from pathlib import Path


class PathSandbox:
    def __init__(self, workspace_root: str | Path = ".", writable_dirs: list[str] | None = None) -> None:
        self._root = Path(workspace_root).resolve()
        self._writable = [self._root / d for d in (writable_dirs or [])]

    @property
    def root(self) -> Path:
        return self._root

    def validate_path(self, path_str: str, workspace_root: str = "") -> bool:
        root = Path(workspace_root or self._root).resolve()
        resolved = (root / path_str).resolve()
        return resolved.is_relative_to(root) and ".." not in Path(path_str).parts

    def has_symlink(self, path_str: str, workspace_root: str = "") -> bool:
        """Check whether any segment of *path_str* (resolved against root) is a symlink.

        Walks each component of the original (non-resolved) path from root downward,
        checking whether a symlink appears before reaching the final target.
        Returns True if the path or any parent up to (but not including) the
        workspace root is a symbolic link.
        """
        root = Path(workspace_root or self._root).resolve()
        full = root / path_str
        # Walk each segment from root downward, checking for symlinks
        # We accumulate segments one at a time and check is_symlink() on the
        # partially-built path BEFORE resolving further.
        parts = Path(path_str).parts
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def resolve_path(self, path_str: str, workspace_root: str = "") -> Path:
        """Resolve *path_str* against root and return the fully resolved Path."""
        root = Path(workspace_root or self._root).resolve()
        return (root / path_str).resolve()

    def validate_command(self, command: str) -> bool:
        dangerous = [";", "&&", "|", "$(", "`", "rm -rf /", "sudo"]
        return not any(d in command for d in dangerous)

    def allowed_dirs(self) -> list[str]:
        return [str(d) for d in self._writable]
