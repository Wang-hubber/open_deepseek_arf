"""DirectoryBoundary — whitelist boundary for path safety checks."""

from __future__ import annotations

import os
from pathlib import Path


class DirectoryBoundary:
    """A whitelist directory boundary for path validation.

    Accepts a single root path or a list of allowed paths.
    PathCheckToolGuard validates paths against this boundary.
    """

    def __init__(self, root: str | Path | list[str]) -> None:
        if isinstance(root, list):
            self._roots = [Path(r).resolve() for r in root]
            self._root = self._roots[0] if self._roots else Path(".").resolve()
        else:
            self._roots = [Path(root).resolve()]
            self._root = self._roots[0]

    @property
    def root(self) -> Path:
        return self._root

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def contains(self, path_str: str) -> bool:
        """Return True if path_str resolves within any allowed root.

        Rejects paths with ``..`` traversal before resolution.
        Resolves *path_str* relative to CWD (via abspath), not by
        joining it to *r*, which would double-prefix when *path_str*
        already starts with a subdirectory inside *r*.
        """
        if ".." in Path(path_str).parts:
            return False
        resolved = Path(os.path.abspath(path_str))
        for r in self._roots:
            if resolved == r or resolved.is_relative_to(r):
                return True
        return False

    def has_symlink(self, path_str: str) -> bool:
        """Check whether any segment of path_str is a symlink.

        Walks each component from the first root downward.
        """
        parts = Path(path_str).parts
        current = self._root
        for part in parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def resolve(self, path_str: str) -> Path:
        """Resolve path_str against default root and return the fully resolved Path."""
        return (self._root / path_str).resolve()

    def __repr__(self) -> str:
        return f"DirectoryBoundary(roots={[str(r) for r in self._roots]})"
