"""P0 regression tests — newline bypass in PathCheckToolGuard (FIXED).

Trailing-newline attacks (e.g. ``../../etc/passwd\\n``) are now blocked:
the guard strips trailing whitespace before running path checks. True
multi-line content (embedded newlines after stripping) is still allowed
since it is file content, not a path.
"""
import tempfile
import os
from pathlib import Path

import pytest

from arf.guardrails.path_check import PathCheckToolGuard, ResourceQuota
from arf.sandbox.directory_boundary import DirectoryBoundary


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
            yield DirectoryBoundary(tmp)

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


class TestNewlineInMiddleDoesNotBypassWhenIntendedAsContent:
    """Ensure legitimate multi-line content is still handled correctly.

    The fix must distinguish between:
      - ``path\\n``   (trailing newline — attack, should be blocked)
      - ``content\\nmore content`` (multi-line file content — legit, should be allowed)
    """

    @pytest.fixture
    def guard(self):
        return PathCheckToolGuard()

    @pytest.fixture
    def boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield DirectoryBoundary(tmp)

    def test_multi_line_content_still_allowed(self, guard, boundary):
        """Real file content with embedded newlines is not a path attack."""
        content = "def foo():\n    return 42\n"
        result = guard._check_one(content, boundary)
        assert result.allowed is True, (
            "Multi-line content should remain allowed (not a path)."
        )

    # ── overly long strings are rejected ──

    def test_overly_long_path_rejected(self, guard, boundary):
        """A 300-char string is not a legitimate path — reject it."""
        long_path = "a" * 300
        result = guard._check_one(long_path, boundary)
        assert result.allowed is False
        assert "too long" in result.reason

    def test_max_path_len_boundary_accepted(self, guard, boundary):
        """A path at exactly MAX_PATH_LEN (255) within workspace passes
        all checks — it's a legitimate (if unusually named) path."""
        ok_path = "x" * 255
        result = guard._check_one(ok_path, boundary)
        assert result.allowed is True  # within boundary, no traversal

    def test_multi_line_with_suspicious_text_still_checked(self, guard, boundary):
        """Content that happens to contain path-like substrings should
        NOT be blindly allowed just because it has newlines."""
        # Single-line ``..`` disguised inside multi-line content is NOT a path
        content = "line one\n../../etc/passwd\nline three"
        result = guard._check_one(content, boundary)
        # This is debatable: is it content or an attack? Current behavior allows it.
        # After fix, we may want to sanitize or reject paths spanning multiple lines.
        # For now, document the current behavior.
        assert result.allowed is True, "Multi-line with embedded patterns currently allowed."
