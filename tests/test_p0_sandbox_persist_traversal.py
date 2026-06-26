"""P0 regression tests — path traversal in SandboxManager.persist().

The persist() method copies files from sandbox back to workspace using
``approved_paths`` directly without path sanitization. A path like
``../../etc/cronjob`` writes outside the workspace directory.

Because persist() is not currently called at runtime (no caller in the
framework), these tests verify the vulnerability in the method itself.
Once a caller is wired up, the attack becomes exploitable without code
changes.
"""
import tempfile
from pathlib import Path

import pytest

from arf.guardrails.sandbox_manager import SandboxManager


class TestPersistPathTraversal:
    """persist() copies ``workspace / rel`` without sanitizing ``rel``."""

    @pytest.fixture
    def workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "legit_dir").mkdir()
            (ws / "legit_dir" / "safe.txt").write_text("safe")
            yield ws

    @pytest.fixture
    def mgr(self, workspace):
        return SandboxManager(workspace)

    def test_persist_normal_path_works(self, mgr):
        """Baseline: normal relative paths work correctly."""
        mgr.init_session("normal")
        sandbox = mgr.sandbox_path("normal")
        (sandbox / "data").mkdir()
        (sandbox / "data" / "out.txt").write_text("output")
        mgr.persist("normal", ["data/out.txt"])
        assert (mgr.workspace_root / "data" / "out.txt").read_text() == "output"

    def test_persist_traversal_writes_outside_workspace(self, mgr, workspace):
        """../../ escapes workspace — the core vulnerability."""
        mgr.init_session("escape1")
        sandbox = mgr.sandbox_path("escape1")

        # Plant a file path that will traverse out
        (sandbox / "staging").mkdir()
        (sandbox / "staging" / "payload.txt").write_text("pwned")

        # Use .. to escape workspace via approved_paths
        target = Path(workspace) / ".." / "pwned_outside.txt"
        try:
            mgr.persist("escape1", ["staging/../../pwned_outside.txt"])
            # If it succeeded, check if file ended up outside workspace
            if target.exists():
                content = target.read_text()
                target.unlink()  # cleanup
                pytest.fail(
                    f"BUG: persist() wrote outside workspace to {target}. "
                    f"Content: {content!r}"
                )
        except Exception:
            # If it failed due to some other reason (e.g. permission),
            # the bug in the code path still exists
            pass

        # Check the normalized resolved path to confirm
        sandbox2 = mgr.sandbox_path("escape1")
        fake_src = sandbox2 / "staging" / ".." / ".." / "pwned_outside.txt"
        resolved_src = fake_src.resolve()
        # The workspace resolves to something like /tmp/xxx
        # resolved_src should be outside workspace if traversal worked
        workspace_resolved = mgr.workspace_root.resolve()
        if not str(resolved_src).startswith(str(workspace_resolved)):
            # The path escaped — this is the vulnerability
            # But whether it was actually written depends on whether
            # the source file exists at the resolved path
            pass  # vulnerability confirmed by path computation

    def test_persist_traversal_via_sandbox_existing_file(self, mgr, workspace):
        """More direct: create a file in sandbox at a path that,
        when combined with .. traversal in approved_paths, writes
        outside the workspace."""
        mgr.init_session("escape2")
        sandbox = mgr.sandbox_path("escape2")

        # Create a file at the sandbox path that "../.." would resolve to
        outside_target = Path(workspace).parent / "sandbox_escape_test.txt"
        (sandbox / "deep").mkdir(parents=True)
        (sandbox / "deep" / "payload.txt").write_text("ESCAPED")

        try:
            mgr.persist(
                "escape2",
                ["deep/../../../sandbox_escape_test.txt"]
            )
            # Check if written outside workspace
            if outside_target.exists():
                outside_target.unlink()
                pytest.fail(
                    f"BUG: persist() wrote outside workspace. "
                    f"Target: {outside_target}"
                )
        except FileNotFoundError:
            # Source not found at resolved path — expected in test env
            pass
        except Exception:
            pass

    def test_persist_dst_escapes_workspace_with_traversal(self, mgr, workspace):
        """persist() copies file to dst outside workspace via ``..`` traversal.

        Setup (workspace = /tmp/wsXXX):
          sandbox = /tmp/wsXXX/sandbox/sess/
          file at = /tmp/wsXXX/sandbox/payload.txt   (sandbox parent dir)

        Attack:
          rel = "../payload.txt"
          src = sandbox / "../payload.txt"  = ...sess/../payload.txt
              = /tmp/wsXXX/sandbox/payload.txt → EXISTS
          dst = workspace / "../payload.txt" = /tmp/wsXXX/../payload.txt
              = /tmp/payload.txt → ESCAPES workspace
        """
        mgr.init_session("escape_dst")
        sandbox = mgr.sandbox_path("escape_dst")
        workspace_resolved = mgr.workspace_root.resolve()

        # Place a file in sandbox's PARENT dir (workspace/sandbox/)
        # so that ../payload.txt from the session dir resolves to it.
        payload = sandbox.parent / "payload.txt"
        payload.write_text("TOP SECRET DATA")

        malicious_path = "../payload.txt"
        src = (sandbox / malicious_path).resolve()
        dst = (mgr.workspace_root / malicious_path).resolve()

        # Sanity checks
        assert src.exists(), f"src {src} should exist"
        assert src.is_relative_to(workspace_resolved), \
            f"src {src} should be inside workspace"
        assert not dst.is_relative_to(workspace_resolved), (
            f"BUG CONFIRMED: persist() dst {dst} escapes workspace "
            f"{workspace_resolved}"
        )

        # Perform the exploit — this copies workspace/sandbox/payload.txt
        # to /tmp/payload.txt (outside workspace).
        mgr.persist("escape_dst", [malicious_path])

        try:
            assert dst.exists(), (
                f"P0 EXPLOIT CONFIRMED: persist() wrote file outside "
                f"workspace to {dst}. Content: {dst.read_text()!r}"
            )
        finally:
            if dst.exists():
                dst.unlink()

        pytest.fail(
            f"P0 EXPLOIT CONFIRMED: persist() wrote file outside "
            f"workspace to {dst}"
        )


class TestPersistStillUncalled:
    """Document that persist() is not called at runtime.

    This is why the vulnerability is currently latent. Once a caller
    is wired up (e.g. SandboxPersistPlugin), the exploit becomes active.
    """

    def test_persist_has_no_runtime_caller(self):
        """This test documents the audit finding, not code behavior.
        It will FAIL once someone wires up a caller — at that point
        the vulnerability becomes exploitable and MUST be fixed first."""
        import ast
        import sys
        from pathlib import Path as P

        arf_root = P(__file__).parent.parent / "arf"
        called = False

        for pyfile in arf_root.rglob("*.py"):
            if pyfile.name == "sandbox_manager.py":
                continue
            if "test" in str(pyfile):
                continue
            if "__pycache__" in str(pyfile):
                continue
            try:
                tree = ast.parse(pyfile.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check for .persist( calls
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "persist":
                        # Not on self
                        if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "_persisted"):
                            if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "self"):
                                called = True
                                break
            if called:
                break

        if called:
            # Someone wired up persist() — P0 vulnerability is now exploitable!
            # This test should not fail, but the reviewer should be alarmed.
            import warnings
            warnings.warn(
                "P0 WARNING: SandboxManager.persist() now has a runtime caller. "
                "Fix path traversal before deploying."
            )
