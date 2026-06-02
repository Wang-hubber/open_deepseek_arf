"""Unit tests for DirectoryBoundary."""

from pathlib import Path
import tempfile
import os
import pytest
from arf.sandbox.directory_boundary import DirectoryBoundary


class TestDirectoryBoundary:
    def test_contains_path_within_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "data").mkdir()
            boundary = DirectoryBoundary(tmp)
            assert boundary.contains("data/file.txt") is True

    def test_contains_path_outside_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            boundary = DirectoryBoundary(tmp)
            assert boundary.contains("/etc/passwd") is False

    def test_contains_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            boundary = DirectoryBoundary(tmp)
            assert boundary.contains("../etc/passwd") is False

    def test_contains_nested_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            boundary = DirectoryBoundary(tmp)
            assert boundary.contains("data/../../etc/passwd") is False

    def test_has_symlink_detects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real").mkdir()
            os.symlink(root / "real", root / "link")
            boundary = DirectoryBoundary(tmp)
            assert boundary.has_symlink("link") is True

    def test_has_symlink_no_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real").mkdir()
            boundary = DirectoryBoundary(tmp)
            assert boundary.has_symlink("real") is False

    def test_resolve_returns_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            boundary = DirectoryBoundary(tmp)
            resolved = boundary.resolve("data/file.txt")
            assert resolved.is_absolute()
            assert str(resolved).startswith(tmp)
