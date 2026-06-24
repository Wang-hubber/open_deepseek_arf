"""Unit tests for DirectoryBoundary."""

from pathlib import Path
import tempfile
import os
import pytest
from arf.sandbox.directory_boundary import DirectoryBoundary


@pytest.fixture
def boundary_root():
    """Temp directory, chdir'd so abspath() resolves relative paths correctly."""
    with tempfile.TemporaryDirectory() as tmp:
        old = os.getcwd()
        os.chdir(tmp)
        try:
            yield Path(tmp)
        finally:
            os.chdir(old)


class TestDirectoryBoundary:
    def test_contains_path_within_boundary(self, boundary_root):
        (boundary_root / "data").mkdir()
        boundary = DirectoryBoundary(str(boundary_root))
        assert boundary.contains("data/file.txt") is True

    def test_contains_path_outside_boundary(self, boundary_root):
        boundary = DirectoryBoundary(str(boundary_root))
        assert boundary.contains("/etc/passwd") is False

    def test_contains_traversal_blocked(self, boundary_root):
        boundary = DirectoryBoundary(str(boundary_root))
        assert boundary.contains("../etc/passwd") is False

    def test_contains_nested_traversal_blocked(self, boundary_root):
        boundary = DirectoryBoundary(str(boundary_root))
        assert boundary.contains("data/../../etc/passwd") is False

    def test_contains_subdir_already_in_path(self, boundary_root):
        """Regression: double-join when path_str already starts with root subdir.

        If root = /workspace and path_str = workspace/sales.html, the old
        code would join them into /workspace/workspace/sales.html.
        """
        (boundary_root / "workspace").mkdir()
        boundary = DirectoryBoundary(str(boundary_root))
        assert boundary.contains("workspace/sales.html") is True

    def test_has_symlink_detects_symlink(self, boundary_root):
        (boundary_root / "real").mkdir()
        os.symlink(boundary_root / "real", boundary_root / "link")
        boundary = DirectoryBoundary(str(boundary_root))
        assert boundary.has_symlink("link") is True

    def test_has_symlink_no_symlink(self, boundary_root):
        (boundary_root / "real").mkdir()
        boundary = DirectoryBoundary(str(boundary_root))
        assert boundary.has_symlink("real") is False

    def test_resolve_returns_absolute(self, boundary_root):
        boundary = DirectoryBoundary(str(boundary_root))
        resolved = boundary.resolve("data/file.txt")
        assert resolved.is_absolute()
        assert str(resolved).startswith(str(boundary_root))
