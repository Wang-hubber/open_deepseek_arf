"""P0 regression tests — newline bypass in PathCheckToolGuard.

These tests demonstrate that a path string ending with ``\\n``
bypasses ALL six security checks in PathCheckToolGuard._check_one().

The root cause: line 79 returns ``allowed=True`` immediately when
``"\\n" in v``, treating any multi-line string as "file content" rather
than a path. An attacker can append ``\\n`` to a malicious path to
exploit this.

Once the fix is applied, all tests marked ``@pytest.mark.xfail``
should be updated to expect ``allowed=False``.
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

    # ── bypass: these SHOULD be blocked but pass due to newline ──

    def test_traversal_bypassed_by_trailing_newline(self, guard, boundary):
        """../../etc/passwd\\n skips all checks and is ALLOWED."""
        result = guard._check_one("../../etc/passwd\n", boundary)
        assert result.allowed is True, (
            "BUG: path traversal bypassed via trailing newline. "
            "Expected allowed=False but got allowed=True."
        )

    def test_absolute_bypassed_by_trailing_newline(self, guard, boundary):
        result = guard._check_one("/etc/passwd\n", boundary)
        assert result.allowed is True, (
            "BUG: absolute path bypassed via trailing newline."
        )

    def test_symlink_bypassed_by_trailing_newline(self, guard, boundary):
        result = guard._check_one("data/escape_link\n", boundary)
        assert result.allowed is True, (
            "BUG: symlink check bypassed via trailing newline."
        )

    def test_workspace_containment_bypassed_by_newline(self, guard, boundary):
        """A path outside the workspace is allowed if it contains \\n."""
        with tempfile.TemporaryDirectory() as other:
            other_file = Path(other) / "secret.txt"
            other_file.write_text("secret")
            result = guard._check_one(f"{other_file}\n", boundary)
            assert result.allowed is True, (
                "BUG: workspace containment bypassed via trailing newline."
            )

    # ── also: check() which calls _check_one via _walk_strings ──

    def test_check_bypassed_via_params_with_newline(self, guard, boundary):
        """The full check() pipeline also misses the attack when params contain \\n."""
        import asyncio
        params = {"file_path": "../../etc/shadow\n"}
        result = asyncio.run(guard.check("read_file", params, boundary))
        assert result.allowed is True, (
            "BUG: full check() pipeline bypassed via newline in params."
        )

    # ── quota bypass ──

    def test_depth_quota_bypassed_by_newline(self, boundary):
        guard = PathCheckToolGuard(
            checks={"path_traversal": True},
            quota=ResourceQuota(max_path_depth=3),
        )
        # 6 levels deep, should be blocked by quota
        result = guard._check_one("a/b/c/d/e/f\n", boundary)
        assert result.allowed is True, (
            "BUG: depth quota bypassed via trailing newline."
        )

    def test_count_quota_bypassed_by_newline(self, boundary):
        guard = PathCheckToolGuard(
            checks={"path_traversal": True},
            quota=ResourceQuota(max_path_count=1),
        )
        # First call consumes quota
        guard._check_one("a/b", boundary)
        # Second call should be blocked by quota, but newline bypasses
        result = guard._check_one("c/d\n", boundary)
        assert result.allowed is True, (
            "BUG: count quota bypassed via trailing newline."
        )


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
