"""Tests for RoundManager — checkpoint, undo, persistence."""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest


def _state(session_id="s1", agent_name="main", messages=None, interaction_round=0, **extra):
    s = {
        "session_id": session_id,
        "agent_name": agent_name,
        "messages": messages or [{"role": "user", "content": "hi"}],
        "current_model": "test",
        "current_turn": 0,
        "interaction_round": interaction_round,
        "context_summary": "",
        "tool_results": {},
        "plan": None,
        "metadata": {},
    }
    s.update(extra)
    return s


@pytest.fixture
def clean_round_manager():
    """
    Build a RoundManager that writes to a temp path, not the real
    memory/checkpoints/ dir that may have leftover data.
    """
    import tempfile
    from arf.plugins.undo.round_manager import RoundManager

    td = tempfile.mkdtemp()
    persist = Path(td) / "rounds.json"

    with patch.object(RoundManager, "_PERSIST_FILE", persist):
        yield RoundManager

    # Cleanup
    import shutil
    shutil.rmtree(td, ignore_errors=True)


class TestRoundManagerBeginRound:
    """Snapshot creation on begin_round()."""

    def test_begin_round_creates_snapshot(self, tmp_path, clean_round_manager):
        rm = clean_round_manager(max_undo_depth=3)
        tx = rm.begin_round(_state(), workspace_dir=str(tmp_path))

        assert tx is not None
        assert tx.round_num == 1
        assert tx.state_snapshot["session_id"] == "s1"
        assert tx.agent_trace == ["main"]
        assert rm.count() == 1
        assert rm.current_round_num == 1

    def test_begin_round_deepcopies_state(self, tmp_path, clean_round_manager):
        rm = clean_round_manager()
        original = _state(messages=[{"role": "user", "content": "hi"}])
        tx = rm.begin_round(original, workspace_dir=str(tmp_path))

        # Mutate original — snapshot must be unaffected
        original["messages"].append({"role": "assistant", "content": "new"})
        original["current_turn"] = 99

        assert len(tx.state_snapshot["messages"]) == 1
        assert tx.state_snapshot["current_turn"] == 0

    def test_begin_round_uses_agent_name_for_trace(self, tmp_path, clean_round_manager):
        rm = clean_round_manager()
        tx = rm.begin_round(
            _state(agent_name="sys_agent"),
            workspace_dir=str(tmp_path),
        )
        assert tx.agent_trace == ["sys_agent"]

    def test_begin_round_increments_round_number(self, tmp_path, clean_round_manager):
        rm = clean_round_manager()
        rm.begin_round(_state(), workspace_dir=str(tmp_path))
        rm.begin_round(_state(), workspace_dir=str(tmp_path))
        assert rm.current_round_num == 2
        assert rm.count() == 2

    def test_active_round_set_on_begin(self, tmp_path, clean_round_manager):
        rm = clean_round_manager()
        tx = rm.begin_round(_state(), workspace_dir=str(tmp_path))
        assert rm.active_round is tx


class TestRoundManagerUndo:
    """Undo restores previous round state."""

    def test_undo_single_step_restores_to_start_of_last_round(self, tmp_path, clean_round_manager):
        """undo(1) pops the latest round and returns its state snapshot (start of that round)."""
        rm = clean_round_manager(max_undo_depth=3)
        s0 = _state(messages=[{"role": "user", "content": "round0"}], interaction_round=0)
        s1 = _state(messages=[{"role": "user", "content": "round1"}], interaction_round=1)

        rm.begin_round(s0, workspace_dir=str(tmp_path))
        rm.begin_round(s1, workspace_dir=str(tmp_path))

        restored = rm.undo(1, workspace_dir=str(tmp_path))
        assert restored is not None
        # Returns the state at the beginning of the popped round (round 1)
        assert restored["interaction_round"] == 1
        assert restored["messages"][0]["content"] == "round1"
        assert rm.count() == 1  # Only s0 remains

    def test_undo_multiple_steps_returns_oldest_popped(self, tmp_path, clean_round_manager):
        """undo(2) from [s0,s1,s2] pops s2 and s1, returns s1 (oldest popped)."""
        rm = clean_round_manager(max_undo_depth=5)
        rm.begin_round(_state(interaction_round=0), workspace_dir=str(tmp_path))
        rm.begin_round(_state(interaction_round=1), workspace_dir=str(tmp_path))
        rm.begin_round(_state(interaction_round=2), workspace_dir=str(tmp_path))

        restored = rm.undo(2, workspace_dir=str(tmp_path))
        # s1 is the oldest popped, so its state is returned
        assert restored["interaction_round"] == 1
        assert rm.count() == 1  # Only s0 remains

    def test_undo_returns_none_when_insufficient_rounds(self, clean_round_manager):
        rm = clean_round_manager()
        assert rm.undo(2) is None
        assert rm.undo(0) is None

    def test_undo_removes_rounds_from_deque(self, tmp_path, clean_round_manager):
        rm = clean_round_manager(max_undo_depth=5)
        rm.begin_round(_state(), workspace_dir=str(tmp_path))
        rm.begin_round(_state(), workspace_dir=str(tmp_path))
        rm.begin_round(_state(), workspace_dir=str(tmp_path))
        assert rm.count() == 3

        rm.undo(2, workspace_dir=str(tmp_path))
        assert rm.count() == 1, f"After undo(2), 1 round should remain, got {rm.count()}"

    def test_undo_clears_active_round(self, tmp_path, clean_round_manager):
        rm = clean_round_manager()
        rm.begin_round(_state(), workspace_dir=str(tmp_path))
        rm.undo(1, workspace_dir=str(tmp_path))
        assert rm.active_round is None

    def test_undo_restores_workspace_from_target_snapshot(self, tmp_path, clean_round_manager):
        """undo(2) from [s0(ws=v1), s1(ws=v2)] restores v1 via s0's snapshot."""
        rm = clean_round_manager(max_undo_depth=3)

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "file1.txt").write_text("version 1")
        (ws / "sub").mkdir()
        (ws / "sub" / "file2.txt").write_text("sub version 1")

        rm.begin_round(_state(), workspace_dir=str(ws))

        # Mutate workspace
        (ws / "file1.txt").write_text("version 2 - modified")
        (ws / "file3.txt").write_text("new file")

        rm.begin_round(_state(interaction_round=1), workspace_dir=str(ws))

        # undo(2): pops s1 then s0, restores from s0's workspace snapshot
        rm.undo(2, workspace_dir=str(ws))

        # After undo(2), workspace should be back to "version 1"
        assert (ws / "file1.txt").read_text() == "version 1"
        assert not (ws / "file3.txt").exists()
        assert (ws / "sub" / "file2.txt").exists()

    def test_undo_skips_git_files(self, tmp_path, clean_round_manager):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "data.txt").write_text("data")
        (ws / ".git").mkdir()
        (ws / ".git" / "config").write_text("git config")

        rm = clean_round_manager(max_undo_depth=3)
        rm.begin_round(_state(), workspace_dir=str(ws))
        # .git files should not be snapshotted or restored
        assert rm.count() == 1


class TestRoundManagerCloseRound:
    """close_round lifecycle."""

    def test_close_round_marks_closed(self, tmp_path, clean_round_manager):
        rm = clean_round_manager()
        rm.begin_round(_state(), workspace_dir=str(tmp_path))
        tx = rm.active_round
        rm.close_round()

        assert tx.closed is True
        assert rm.active_round is None

    def test_close_round_noop_without_active(self, clean_round_manager):
        rm = clean_round_manager()
        rm.close_round()  # Should not raise


class TestRoundManagerMaxDepth:
    """Sliding window when exceeding max_undo_depth."""

    def test_max_depth_evicts_oldest_rounds(self, tmp_path, clean_round_manager):
        rm = clean_round_manager(max_undo_depth=2)
        rm.begin_round(_state(interaction_round=0), workspace_dir=str(tmp_path))
        rm.begin_round(_state(interaction_round=1), workspace_dir=str(tmp_path))
        rm.begin_round(_state(interaction_round=2), workspace_dir=str(tmp_path))

        assert rm.count() == 2, f"max_undo_depth=2 should cap at 2, got {rm.count()}"
        # Round 0 was evicted; remaining are rounds 1 and 2.
        # undo(1) pops round 2, returns its snapshot (ir=2).
        restored = rm.undo(1, workspace_dir=str(tmp_path))
        assert restored["interaction_round"] == 2


class TestRoundManagerPersistence:
    """Save/restore round metadata across process restarts."""

    def test_save_and_restore_from_disk(self, tmp_path):
        import tempfile, shutil

        td = tempfile.mkdtemp()
        persist = Path(td) / "rounds.json"
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "file.txt").write_text("hello")

        from arf.plugins.undo.round_manager import RoundManager

        with patch.object(RoundManager, "_PERSIST_FILE", persist):
            rm = RoundManager(max_undo_depth=3)
            rm.begin_round(_state(interaction_round=0), workspace_dir=str(ws))
            rm.close_round()

        # Create a new RoundManager — should restore from disk
        with patch.object(RoundManager, "_PERSIST_FILE", persist):
            rm2 = RoundManager(max_undo_depth=3)
            assert rm2.current_round_num == 1
            assert rm2.count() == 1
            assert rm2.active_round is None  # was closed

        shutil.rmtree(td, ignore_errors=True)

    def test_restore_preserves_undo_capability(self, tmp_path):
        import tempfile, shutil

        td = tempfile.mkdtemp()
        persist = Path(td) / "rounds.json"
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "v1.txt").write_text("v1")

        from arf.plugins.undo.round_manager import RoundManager

        with patch.object(RoundManager, "_PERSIST_FILE", persist):
            rm = RoundManager(max_undo_depth=3)
            rm.begin_round(_state(interaction_round=0), workspace_dir=str(ws))

            (ws / "v1.txt").write_text("v2")
            rm.begin_round(_state(interaction_round=1), workspace_dir=str(ws))

        # Restore: 2 rounds persisted. undo(2) pops both, restores from s0's snapshot.
        with patch.object(RoundManager, "_PERSIST_FILE", persist):
            rm2 = RoundManager(max_undo_depth=3)
            restored = rm2.undo(2, workspace_dir=str(ws))

        assert restored is not None
        assert restored["interaction_round"] == 0
        assert (ws / "v1.txt").read_text() == "v1"

        shutil.rmtree(td, ignore_errors=True)

    def test_corrupted_index_handled_gracefully(self, tmp_path):
        import tempfile, shutil

        td = tempfile.mkdtemp()
        persist = Path(td) / "rounds.json"
        persist.parent.mkdir(parents=True, exist_ok=True)
        persist.write_text("not valid json {{{", encoding="utf-8")

        from arf.plugins.undo.round_manager import RoundManager

        with patch.object(RoundManager, "_PERSIST_FILE", persist):
            rm = RoundManager()
            assert rm.count() == 0  # Should start empty

        shutil.rmtree(td, ignore_errors=True)

    def test_missing_index_file(self):
        import tempfile, shutil

        td = tempfile.mkdtemp()
        persist = Path(td) / "nonexistent" / "rounds.json"

        from arf.plugins.undo.round_manager import RoundManager

        with patch.object(RoundManager, "_PERSIST_FILE", persist):
            rm = RoundManager()
            assert rm.count() == 0  # No disk state, start fresh

        shutil.rmtree(td, ignore_errors=True)


class TestRoundTransactionDefaults:
    """RoundTransaction dataclass defaults."""

    def test_defaults(self):
        from arf.plugins.undo.round_manager import RoundTransaction

        tx = RoundTransaction(
            round_id="s1/0",
            round_num=0,
            state_snapshot={"session_id": "s1"},
        )
        assert tx.agent_trace == []
        assert tx.closed is False
        assert tx.workspace_snapshot_dir is None
        assert tx.created_at > 0
