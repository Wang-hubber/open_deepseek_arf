"""Integration tests for tool directory boundary in executor pipeline."""

import tempfile
from pathlib import Path

import pytest

from arf.guardrails.path_check import PathCheckToolGuard
from arf.sandbox.directory_boundary import DirectoryBoundary


class TestPathCheckWithBoundary:
    """PathCheckToolGuard with DirectoryBoundary integration."""

    @pytest.fixture
    def guard(self):
        return PathCheckToolGuard()

    @pytest.fixture
    def workspace_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield DirectoryBoundary(tmp)

    def test_tool_inherits_workspace_boundary(self, guard, workspace_boundary):
        result = guard._check_one("data/file.txt", workspace_boundary)
        assert result.allowed is True

    def test_tool_blocked_outside_workspace(self, guard, workspace_boundary):
        result = guard._check_one("/etc/passwd", workspace_boundary)
        assert result.allowed is False
        assert "blocked" in result.reason

    def test_tool_with_elevated_boundary_can_access_wider(self, guard):
        with tempfile.TemporaryDirectory() as tmp:
            boundary = DirectoryBoundary(tmp)
            result = guard._check_one("data/file.txt", boundary)
            assert result.allowed is True

    def test_tool_with_elevated_boundary_still_blocks_traversal(self, guard):
        with tempfile.TemporaryDirectory() as tmp:
            boundary = DirectoryBoundary(tmp)
            result = guard._check_one("data/../../etc/passwd", boundary)
            assert result.allowed is False

    def test_absolute_path_blocked_even_in_elevated_boundary(self, guard):
        with tempfile.TemporaryDirectory() as tmp:
            boundary = DirectoryBoundary(tmp)
            result = guard._check_one("/etc/passwd", boundary)
            assert result.allowed is False

    def test_multiline_content_skipped(self, guard, workspace_boundary):
        content = "line1\nline2\n/etc/passwd\nline3"
        result = guard._check_one(content, workspace_boundary)
        assert result.allowed is True  # multiline = file content, not path


class TestBoundaryResolution:
    """Executor boundary resolution logic."""

    def test_tool_with_allowed_dir_gets_elevated(self):
        with (
            tempfile.TemporaryDirectory() as ws,
            tempfile.TemporaryDirectory() as uploads,
        ):
            tool_boundaries = {"file_writer": DirectoryBoundary(uploads)}
            default_boundary = DirectoryBoundary(ws)

            # file_writer uses uploads boundary
            bw = tool_boundaries.get("file_writer", default_boundary)
            assert bw.root == Path(uploads).resolve()

            # grep inherits default
            bw = tool_boundaries.get("grep", default_boundary)
            assert bw.root == Path(ws).resolve()

    def test_tool_without_allowed_dir_inherits_default(self):
        with tempfile.TemporaryDirectory() as ws:
            tool_boundaries = {}
            default_boundary = DirectoryBoundary(ws)

            bw = tool_boundaries.get("some_tool", default_boundary)
            assert bw.root == Path(ws).resolve()
