"""DirectoryBoundary — whitelist boundary for path safety checks."""

from __future__ import annotations

from pathlib import Path


class DirectoryBoundary:
    """A whitelist directory boundary for path validation.

    Tools use this to declare their safe operating directory.
    PathCheckToolGuard validates paths against this boundary.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def contains(self, path_str: str) -> bool:
        """Return True if path_str resolves within root.

        Rejects paths with ``..`` traversal before resolution.
        """
        if ".." in Path(path_str).parts:
            return False
        resolved = (self._root / path_str).resolve()
        return resolved.is_relative_to(self._root)

    def has_symlink(self, path_str: str) -> bool:
        """Check whether any segment of path_str is a symlink.

        Walks each component from root downward, checking is_symlink()
        before resolving further.
        """
        parts = Path(path_str).parts
        current = self._root
        for part in parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def resolve(self, path_str: str) -> Path:
        """Resolve path_str against root and return the fully resolved Path."""
        return (self._root / path_str).resolve()

    def __repr__(self) -> str:
        return f"DirectoryBoundary(root={self._root})"
