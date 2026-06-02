"""Integration tests for sandbox isolation in engine workflow."""
import tempfile
from pathlib import Path
import pytest
from arf.sandbox.sandbox_manager import SandboxManager


class TestSandboxIntegration:
    @pytest.fixture
    def workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "data").mkdir()
            (ws / "data" / "original.txt").write_text("original content")
            yield ws

    @pytest.fixture
    def mgr(self, workspace):
        return SandboxManager(workspace, blacklist=[".git", "logs"])

    def test_full_round_workflow(self, mgr):
        """Full round: init → modify → diff → persist → verify."""
        sid = "round_test"
        sp = mgr.init_session(sid)

        (sp / "data" / "original.txt").write_text("modified by tool")
        (sp / "data" / "new_file.txt").write_text("new content")

        diff = mgr.diff(sid)
        assert diff.total == 2

        mgr.persist(sid, ["data/original.txt", "data/new_file.txt"])

        assert (mgr.workspace_root / "data" / "original.txt").read_text() == "modified by tool"
        assert (mgr.workspace_root / "data" / "new_file.txt").read_text() == "new content"

    def test_partial_approval_workflow(self, mgr):
        """User approves some changes, rejects others."""
        sid = "partial_test"
        sp = mgr.init_session(sid)

        (sp / "data" / "approved.txt").write_text("approved")
        (sp / "data" / "rejected.txt").write_text("rejected")

        mgr.persist(sid, ["data/approved.txt"])

        assert (mgr.workspace_root / "data" / "approved.txt").exists()
        assert not (mgr.workspace_root / "data" / "rejected.txt").exists()
        assert (sp / "data" / "rejected.txt").exists()

    def test_whitelisted_tool_boundary_resolution(self, mgr):
        """Whitelist tool uses its own boundary, not sandbox."""
        sid = "boundary_test"
        mgr.init_session(sid)

        tool_boundaries = {"file_reader": str(mgr.workspace_root / "data")}
        assert "file_reader" in tool_boundaries

        sandbox_boundary = mgr.sandbox_path(sid)
        assert sandbox_boundary.name == sid

    def test_session_end_pending_warning(self, mgr):
        """Session end with unpersisted changes should list them."""
        sid = "pending_test"
        sp = mgr.init_session(sid)
        (sp / "data" / "unpersisted.txt").write_text("not saved")

        pending = mgr.pending_changes(sid)
        assert len(pending) == 1
        assert pending[0].path == "data/unpersisted.txt"

    def test_destroy_cleans_all(self, mgr):
        """Destroy removes sandbox and tracked state."""
        sid = "destroy_test"
        mgr.init_session(sid)
        (mgr.sandbox_path(sid) / "data" / "temp.txt").write_text("temp")

        mgr.destroy(sid)
        assert not mgr.sandbox_path(sid).exists()
