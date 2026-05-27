import time
from pathlib import Path

from arf.resources.file_watcher import FileWatcher


class TestAddRemoveWatch:
    def test_add_registers_path(self):
        w = FileWatcher(poll_interval=60)
        w.add_watch(Path("/tmp/test"), lambda p: None)
        assert Path("/tmp/test") in w._watched

    def test_remove_stops_tracking(self, temp_root):
        w = FileWatcher(poll_interval=0.1)
        w.add_watch(temp_root, lambda p: None)
        w.remove_watch(temp_root)
        assert temp_root not in w._watched


class TestPollOnce:
    def test_no_files_returns_empty(self, temp_root):
        w = FileWatcher(poll_interval=0.1)
        w.add_watch(temp_root, lambda p: None)
        assert w._poll_once() == set()

    def test_detects_new_file(self, temp_root):
        w = FileWatcher(poll_interval=0.1)
        w.add_watch(temp_root, lambda p: None)
        w._poll_once()  # seed

        (temp_root / "new.yaml").write_text("name: test")
        changed = w._poll_once()
        assert temp_root in changed

    def test_detects_modified_file(self, temp_root):
        f = temp_root / "existing.yaml"
        f.write_text("v1")
        time.sleep(0.01)

        w = FileWatcher(poll_interval=0.1)
        w.add_watch(temp_root, lambda p: None)
        w._poll_once()  # seed mtimes

        f.write_text("v2")
        changed = w._poll_once()
        assert temp_root in changed

    def test_detects_deleted_file(self, temp_root):
        f = temp_root / "gone.yaml"
        f.write_text("will delete")

        w = FileWatcher(poll_interval=0.1)
        w.add_watch(temp_root, lambda p: None)
        w._poll_once()  # seed
        f.unlink()

        changed = w._poll_once()
        assert temp_root in changed

    def test_no_false_positive_when_unchanged(self, temp_root):
        (temp_root / "stable.yaml").write_text("unchanged")

        w = FileWatcher(poll_interval=0.1)
        w.add_watch(temp_root, lambda p: None)
        w._poll_once()  # seed
        changed = w._poll_once()
        assert changed == set()
