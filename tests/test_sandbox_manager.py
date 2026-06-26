"""Unit tests for SandboxManager."""
import tempfile
from pathlib import Path
import pytest
from arf.guardrails.sandbox_manager import SandboxManager, SandboxDiff, FileChange


class TestSandboxManager:
    @pytest.fixture
    def workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "data").mkdir()
            (ws / "data" / "file.txt").write_text("hello")
            (ws / ".git").mkdir()
            (ws / ".git" / "config").write_text("git config")
            (ws / "logs").mkdir()
            (ws / "logs" / "app.log").write_text("log entry")
            yield ws

    @pytest.fixture
    def mgr(self, workspace):
        return SandboxManager(workspace, blacklist=[".git", "logs"])

    def test_init_session_copies_workspace(self, mgr):
        path = mgr.init_session("test1")
        assert path.exists()
        assert (path / "data" / "file.txt").read_text() == "hello"

    def test_init_session_excludes_blacklist(self, mgr):
        path = mgr.init_session("test2")
        assert not (path / ".git").exists()
        assert not (path / "logs").exists()

    def test_sandbox_path_returns_correct(self, mgr):
        assert mgr.sandbox_path("s1") == mgr.workspace_root / "sandbox" / "s1"

    def test_diff_detects_added_files(self, mgr):
        mgr.init_session("test3")
        (mgr.sandbox_path("test3") / "data" / "new.txt").write_text("new")
        diff = mgr.diff("test3")
        assert any(c.path == "data/new.txt" for c in diff.added)

    def test_diff_detects_modified_files(self, mgr):
        mgr.init_session("test4")
        (mgr.sandbox_path("test4") / "data" / "file.txt").write_text("modified")
        diff = mgr.diff("test4")
        assert any(c.path == "data/file.txt" for c in diff.modified)

    def test_persist_writes_approved_to_workspace(self, mgr):
        mgr.init_session("test5")
        p = mgr.sandbox_path("test5") / "data" / "file.txt"
        p.write_text("approved content")
        mgr.persist("test5", ["data/file.txt"])
        assert (mgr.workspace_root / "data" / "file.txt").read_text() == "approved content"

    def test_destroy_removes_sandbox(self, mgr):
        mgr.init_session("test6")
        mgr.destroy("test6")
        assert not mgr.sandbox_path("test6").exists()

    def test_pending_changes_lists_unpersisted(self, mgr):
        mgr.init_session("test7")
        (mgr.sandbox_path("test7") / "new.txt").write_text("pending")
        pending = mgr.pending_changes("test7")
        assert any(c.path == "new.txt" for c in pending)

    def test_auto_destroy_flag(self):
        mgr = SandboxManager("/tmp", auto_destroy=True)
        assert mgr.auto_destroy is True

    def test_sandbox_not_copied_into_sandbox(self, mgr):
        """Sandbox directory should not be recursively copied."""
        mgr.init_session("a")
        (mgr.workspace_root / "sandbox" / "old_session").mkdir(parents=True, exist_ok=True)
        (mgr.workspace_root / "sandbox" / "old_session" / "data.txt").write_text("old")
        path = mgr.init_session("b")
        assert not (path / "sandbox").exists()
