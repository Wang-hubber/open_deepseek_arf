"""P0 regression tests — newline bypass in PathCheckToolGuard (FIXED).

Trailing-newline attacks (e.g. ``../../etc/passwd\\n``) are now blocked:
the guard strips trailing whitespace before running path checks. True
multi-line content (embedded newlines after stripping) is still allowed
since it is file content, not a path.

Also verifies path_param_names filtering — only explicitly marked
path parameters are checked when the filter is provided.
"""
import tempfile
import os
from pathlib import Path

import pytest

from arf.guardrails.path_check import PathCheckToolGuard, ResourceQuota
from arf.guardrails.directory_boundary import DirectoryBoundary


class TestNewlineBypassPathTraversal:
    """Path traversal (``..``) is caught by ToolGuardPlugin's inline check,
    so these are the second-line-of-defense bypass tests."""

    @pytest.fixture
    def guard(self):
        return PathCheckToolGuard(checks={
            "path_traversal": True,
            "absolute_path": True,
            "workspace_containment": True,
            "symlink": True,
        })

    @pytest.fixture
    def boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            # create a symlink inside the boundary to test symlink detection
            (Path(tmp) / "data").mkdir()
            os.symlink("/etc/passwd", Path(tmp) / "data" / "escape_link")
            old = os.getcwd()
            os.chdir(tmp)
            try:
                yield DirectoryBoundary(tmp)
            finally:
                os.chdir(old)

    # ── baseline: without newline, attacks are blocked ──

    def test_traversal_blocked_without_newline(self, guard, boundary):
        result = guard._check_one("../../etc/passwd", boundary)
        assert result.allowed is False, (
            f"Expected blocked but got allowed. Reason: {result.reason!r}")

    def test_absolute_blocked_without_newline(self, guard, boundary):
        result = guard._check_one("/etc/passwd", boundary)
        assert result.allowed is False

    def test_outside_workspace_blocked_without_newline(self, guard, boundary):
        """Path that stays within boundary filesystem but is a different directory."""
        with tempfile.TemporaryDirectory() as other:
            other_file = Path(other) / "secret.txt"
            other_file.write_text("secret")
            result = guard._check_one(str(other_file), boundary)
            assert result.allowed is False, (
                f"Expected blocked but got allowed. Reason: {result.reason!r}")

    def test_symlink_blocked_without_newline(self, guard, boundary):
        result = guard._check_one("data/escape_link", boundary)
        assert result.allowed is False

    # ── fixed: trailing-newline attacks are now blocked ──

    def test_traversal_blocked_with_trailing_newline(self, guard, boundary):
        result = guard._check_one("../../etc/passwd\n", boundary)
        assert result.allowed is False

    def test_absolute_blocked_with_trailing_newline(self, guard, boundary):
        result = guard._check_one("/etc/passwd\n", boundary)
        assert result.allowed is False

    def test_symlink_blocked_with_trailing_newline(self, guard, boundary):
        result = guard._check_one("data/escape_link\n", boundary)
        assert result.allowed is False

    def test_workspace_containment_blocked_with_trailing_newline(self, guard, boundary):
        with tempfile.TemporaryDirectory() as other:
            other_file = Path(other) / "secret.txt"
            other_file.write_text("secret")
            result = guard._check_one(f"{other_file}\n", boundary)
            assert result.allowed is False

    # ── also: check() pipeline ──

    def test_check_blocked_via_params_with_newline(self, guard, boundary):
        import asyncio
        params = {"file_path": "../../etc/shadow\n"}
        result = asyncio.run(guard.check("read_file", params, boundary))
        assert result.allowed is False

    # ── quota enforcement ──

    def test_depth_quota_enforced_with_trailing_newline(self, boundary):
        guard = PathCheckToolGuard(
            checks={"path_traversal": True},
            quota=ResourceQuota(max_path_depth=3),
        )
        result = guard._check_one("a/b/c/d/e/f\n", boundary)
        assert result.allowed is False

    def test_count_quota_enforced_with_trailing_newline(self, boundary):
        guard = PathCheckToolGuard(
            checks={"path_traversal": True},
            quota=ResourceQuota(max_path_count=1),
        )
        guard._check_one("a/b", boundary)
        result = guard._check_one("c/d\n", boundary)
        assert result.allowed is False


class TestRejectIllegalChars:
    """Null bytes, newlines, and overly-long strings are rejected
    outright — they cannot appear in legitimate paths. Content strings
    are handled by path_param_names filtering at the executor layer."""

    @pytest.fixture
    def guard(self):
        return PathCheckToolGuard()

    @pytest.fixture
    def boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                yield DirectoryBoundary(tmp)
            finally:
                os.chdir(old)

    def test_multi_line_content_rejected(self, guard, boundary):
        """Any string with embedded newlines is rejected — newlines cannot
        appear in a legitimate path. Content strings are handled by
        path_param_names filtering at the executor layer."""
        result = guard._check_one("def foo():\n    return 42\n", boundary)
        assert result.allowed is False
        assert "newline" in result.reason

    def test_null_byte_rejected(self, guard, boundary):
        result = guard._check_one("safe\0/etc/passwd", boundary)
        assert result.allowed is False
        assert "null" in result.reason

    def test_overly_long_path_rejected(self, guard, boundary):
        long_path = "a" * 300
        result = guard._check_one(long_path, boundary)
        assert result.allowed is False
        assert "too long" in result.reason

    def test_max_path_len_boundary_accepted(self, guard, boundary):
        ok_path = "x" * 255
        result = guard._check_one(ok_path, boundary)
        assert result.allowed is True

    def test_embedded_traversal_with_newline_rejected(self, guard, boundary):
        result = guard._check_one("line one\n../../etc/passwd\nline three", boundary)
        assert result.allowed is False
        assert "newline" in result.reason


class TestPathParamFiltering:
    """When path_param_names is provided, only marked params are checked."""

    @pytest.fixture
    def guard(self):
        return PathCheckToolGuard()

    @pytest.fixture
    def boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                yield DirectoryBoundary(tmp)
            finally:
                os.chdir(old)

    def test_checks_only_marked_params(self, guard, boundary):
        """file_path=/etc/passwd is checked; content=/etc/passwd is skipped."""
        import asyncio
        params = {
            "file_path": "/etc/passwd",
            "content": "/etc/passwd",  # same string, but not a path param
        }
        result = asyncio.run(
            guard.check("write_file", params, boundary,
                        path_param_names={"file_path"}))
        # file_path is absolute → blocked
        assert result.allowed is False

    def test_unmarked_absolute_param_is_allowed(self, guard, boundary):
        """If no path_param_names, all strings are checked (backward compat)."""
        import asyncio
        params = {
            "file_path": "safe.txt",
            "content": "/etc/passwd",
        }
        result = asyncio.run(
            guard.check("write_file", params, boundary))
        # content is also checked → blocked
        assert result.allowed is False

    def test_marked_params_only_check_named_keys(self, guard, boundary):
        """Nested values under unmarked keys are skipped entirely."""
        import asyncio
        params = {
            "file_path": "safe.txt",
            "metadata": {"template": "/etc/nginx/nginx.conf"},  # nested, unmarked
        }
        result = asyncio.run(
            guard.check("render", params, boundary,
                        path_param_names={"file_path"}))
        assert result.allowed is True  # metadata.template not checked
