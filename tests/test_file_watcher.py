"""Tests for FileWatcher — poll mode (deterministic, no inotify needed)."""
import tempfile
from pathlib import Path
from arf.resources.file_watcher import FileWatcher


def test_add_watch_registers_path():
    w = FileWatcher(poll_interval=60)
    w.add_watch(Path("/tmp/test"), lambda p: None)
    assert Path("/tmp/test") in w._watched


def test_poll_detects_new_file():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        w = FileWatcher(poll_interval=0.1)
        w.add_watch(root, lambda p: None)

        # No files yet — poll returns empty
        assert w._poll_once() == set()

        # Create a file
        (root / "new.yaml").write_text("name: test")

        changed = w._poll_once()
        assert root in changed


def test_poll_detects_modified_file():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "existing.yaml").write_text("v1")
        import time; time.sleep(0.01)

        w = FileWatcher(poll_interval=0.1)
        w.add_watch(root, lambda p: None)
        w._poll_once()  # seed mtimes

        (root / "existing.yaml").write_text("v2")

        changed = w._poll_once()
        assert root in changed


def test_poll_detects_deleted_file():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = root / "gone.yaml"
        p.write_text("will delete")

        w = FileWatcher(poll_interval=0.1)
        w.add_watch(root, lambda p: None)
        w._poll_once()  # seed
        p.unlink()

        changed = w._poll_once()
        assert root in changed


def test_remove_watch_stops_tracking():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        w = FileWatcher(poll_interval=0.1)
        w.add_watch(root, lambda p: None)
        w.remove_watch(root)
        assert root not in w._watched


def test_no_false_positive_when_unchanged():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "stable.yaml").write_text("unchanged")

        w = FileWatcher(poll_interval=0.1)
        w.add_watch(root, lambda p: None)
        w._poll_once()  # seed
        changed = w._poll_once()
        assert changed == set()
