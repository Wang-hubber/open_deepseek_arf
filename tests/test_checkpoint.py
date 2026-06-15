"""Tests for StateStore implementations — checkpoint, atomic write, crash recovery."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest


class TestInMemoryStateStore:
    """InMemoryStateStore — used in tests and for ephemeral sessions."""

    def test_put_and_get(self):
        from arf.engine.checkpoint import InMemoryStateStore

        async def _test():
            store = InMemoryStateStore()
            state = {"session_id": "s1", "messages": []}
            await store.put("s1", state)
            result = await store.get("s1")
            assert result["session_id"] == "s1"

        asyncio.run(_test())

    def test_put_deepcopies_state(self):
        from arf.engine.checkpoint import InMemoryStateStore

        async def _test():
            store = InMemoryStateStore()
            state = {"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]}
            await store.put("s1", state)

            # Mutate original
            state["messages"].append({"role": "assistant", "content": "new"})

            stored = await store.get("s1")
            assert len(stored["messages"]) == 1  # Snapshot is independent

        asyncio.run(_test())

    def test_get_nonexistent_returns_none(self):
        from arf.engine.checkpoint import InMemoryStateStore

        async def _test():
            store = InMemoryStateStore()
            assert await store.get("nonexistent") is None

        asyncio.run(_test())

    def test_delete_removes_state(self):
        from arf.engine.checkpoint import InMemoryStateStore

        async def _test():
            store = InMemoryStateStore()
            await store.put("s1", {"session_id": "s1"})
            await store.delete("s1")
            assert await store.get("s1") is None

        asyncio.run(_test())

    def test_delete_nonexistent_no_error(self):
        from arf.engine.checkpoint import InMemoryStateStore

        async def _test():
            store = InMemoryStateStore()
            await store.delete("nonexistent")

        asyncio.run(_test())  # Should not raise

    def test_reset_clears_everything(self):
        from arf.engine.checkpoint import InMemoryStateStore

        async def _test():
            store = InMemoryStateStore()
            await store.put("s1", {"session_id": "s1"})
            store.snapshots.append({"session_id": "s2", "turn": 1})

            store.reset()
            assert await store.get("s1") is None
            assert store.snapshots == []

        asyncio.run(_test())

    def test_snapshots_record_put_calls(self):
        from arf.engine.checkpoint import InMemoryStateStore

        async def _test():
            store = InMemoryStateStore()
            await store.put("s1", {"session_id": "s1", "current_turn": 0})
            await store.put("s1", {"session_id": "s1", "current_turn": 1})
            await store.put("s2", {"session_id": "s2", "current_turn": 3})

            assert len(store.snapshots) == 3
            assert store.snapshots[0]["turn"] == 0
            assert store.snapshots[1]["turn"] == 1
            assert store.snapshots[2]["turn"] == 3

        asyncio.run(_test())


class TestFileStateStore:
    """FileStateStore — persists to JSON files, survives restarts."""

    @pytest.fixture
    def data_dir(self, tmp_path):
        return tmp_path

    def test_put_creates_file(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            await store.put("s1", {"session_id": "s1", "messages": []})
            expected_file = data_dir / "s1" / "state" / "s1.json"
            assert expected_file.exists()

        asyncio.run(_test())

    def test_put_and_get_roundtrip(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            original = {
                "session_id": "s1",
                "agent_name": "test",
                "messages": [{"role": "user", "content": "hi"}],
                "current_turn": 5,
                "interaction_round": 3,
                "session_active": True,
            }
            await store.put("s1", original)
            result = await store.get("s1")

            assert result["session_id"] == "s1"
            assert result["current_turn"] == 5
            assert result["interaction_round"] == 3
            assert result["session_active"] is True

        asyncio.run(_test())

    def test_put_omits_tool_results(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            await store.put("s1", {
                "session_id": "s1",
                "messages": [],
                "tool_results": {"call_1": {"success": True, "data": "sensitive"}},
            })

            # Read the raw file
            raw = json.loads((data_dir / "s1" / "state" / "s1.json").read_text(encoding="utf-8"))
            assert "tool_results" not in raw, (
                "tool_results must NOT be persisted across restarts"
            )

        asyncio.run(_test())

    def test_put_original_dict_not_mutated(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            original = {
                "session_id": "s1",
                "messages": [],
                "tool_results": {"call_1": {"success": True}},
            }
            await store.put("s1", original)

            # Original dict should still have tool_results (deepcopy was used)
            assert "tool_results" in original
            assert original["tool_results"]["call_1"]["success"] is True

        asyncio.run(_test())

    def test_atomic_write_no_tmp_residue(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            await store.put("s1", {"session_id": "s1", "messages": []})

            # No .tmp file should be left behind
            tmp_files = list((data_dir / "s1" / "state").glob("*.tmp"))
            assert len(tmp_files) == 0, f"Temporary files left behind: {tmp_files}"

        asyncio.run(_test())

    def test_get_nonexistent_returns_none(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            assert await store.get("nonexistent") is None

        asyncio.run(_test())

    def test_corrupted_json_returns_none(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            bad_state_dir = data_dir / "bad" / "state"
            bad_state_dir.mkdir(parents=True, exist_ok=True)
            (bad_state_dir / "bad.json").write_text("this is not json {{{", encoding="utf-8")

            store = FileStateStore(data_dir)
            result = await store.get("bad")
            assert result is None, "Corrupted JSON should return None gracefully"

        asyncio.run(_test())

    def test_overwrite_updates_file(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            await store.put("s1", {"session_id": "s1", "current_turn": 0})
            await store.put("s1", {"session_id": "s1", "current_turn": 5})

            result = await store.get("s1")
            assert result["current_turn"] == 5

        asyncio.run(_test())

    def test_delete_removes_file(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            await store.put("s1", {"session_id": "s1", "messages": []})
            await store.delete("s1")

            assert not (data_dir / "s1" / "state" / "s1.json").exists()

        asyncio.run(_test())

    def test_delete_nonexistent_no_error(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            await store.delete("nonexistent")

        asyncio.run(_test())  # Should not raise

    def test_multiple_sessions_independent(self, data_dir):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            store = FileStateStore(data_dir)
            await store.put("s1", {"session_id": "s1", "current_turn": 1})
            await store.put("s2", {"session_id": "s2", "current_turn": 2})

            s1 = await store.get("s1")
            s2 = await store.get("s2")
            assert s1["current_turn"] == 1
            assert s2["current_turn"] == 2

        asyncio.run(_test())

    def test_state_dir_auto_created(self, tmp_path):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            auto_dir = tmp_path / "will_be_created"
            store = FileStateStore(auto_dir)
            await store.put("s1", {"session_id": "s1", "messages": []})
            assert auto_dir.exists()

        asyncio.run(_test())


class TestCrashRecoveryScenario:
    """End-to-end crash recovery: session_active flag lifecycle."""

    def test_session_active_persisted_and_detected(self, tmp_path):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            data_dir = tmp_path
            store = FileStateStore(data_dir)

            # Simulate a running session
            await store.put("s1", {
                "session_id": "s1",
                "messages": [{"role": "user", "content": "unfinished work"}],
                "session_active": True,
                "interaction_round": 2,
                "current_turn": 7,
            })

            # Later: another process reads the state
            recovered = await store.get("s1")
            assert recovered["session_active"] is True
            assert recovered["interaction_round"] == 2
            assert recovered["current_turn"] == 7

        asyncio.run(_test())

    def test_session_clean_shutdown_marks_inactive(self, tmp_path):
        from arf.engine.checkpoint import FileStateStore

        async def _test():
            data_dir = tmp_path
            store = FileStateStore(data_dir)

            # Normal shutdown: mark inactive
            await store.put("s1", {
                "session_id": "s1",
                "messages": [],
                "session_active": False,
            })

            recovered = await store.get("s1")
            assert recovered["session_active"] is False

        asyncio.run(_test())
