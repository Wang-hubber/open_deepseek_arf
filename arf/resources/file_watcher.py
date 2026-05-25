"""FileWatcher — cross-platform filesystem change detection.

Linux: inotify via ctypes (sub-second detection).
Other platforms: polling loop comparing os.stat mtime.
"""
import asyncio
import logging
import os
import select
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("arf.file_watcher")

Callback = Callable[[set[Path]], None]


class FileWatcher:
    def __init__(self, poll_interval: float = 5.0):
        self._poll_interval = poll_interval
        self._watched: dict[Path, list[Callback]] = {}
        # Maps watched directory -> {file_path: mtime} for all tracked files
        self._mtimes: dict[Path, dict[Path, float]] = {}
        self._task: asyncio.Task | None = None

    # -- public API --

    def add_watch(self, path: Path, callback: Callback) -> None:
        """Watch a directory for file changes. Callback fires with changed paths."""
        path = path.resolve()
        if path not in self._watched:
            self._watched[path] = []
            self._seed_mtimes(path)
        self._watched[path].append(callback)

    def remove_watch(self, path: Path) -> None:
        path = path.resolve()
        self._watched.pop(path, None)
        self._mtimes.pop(path, None)

    async def start(self) -> None:
        if self._task is not None:
            return
        if sys.platform == "linux":
            self._task = asyncio.create_task(self._inotify_loop())
        else:
            self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "FileWatcher started (mode=%s)",
            "inotify" if sys.platform == "linux" else "poll",
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # -- internal (public for testing) --

    def _seed_mtimes(self, path: Path) -> None:
        """Snapshot all file mtimes under *path*."""
        snapshot: dict[Path, float] = {}
        if path.exists():
            for f in path.rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    snapshot[f] = f.stat().st_mtime
        self._mtimes[path] = snapshot

    def _poll_once(self) -> set[Path]:
        """Compare current filesystem state against last snapshot.

        Returns set of watched directories that have changes.
        """
        changed: set[Path] = set()
        for path in list(self._watched.keys()):
            old_snapshot = self._mtimes.get(path, {})
            new_snapshot: dict[Path, float] = {}

            if not path.exists():
                # Directory disappeared entirely
                if old_snapshot:
                    changed.add(path)
                    self._mtimes[path] = {}
                continue

            # Build current snapshot
            for f in path.rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    new_snapshot[f] = f.stat().st_mtime

            # Detect additions, modifications, deletions
            if new_snapshot != old_snapshot:
                changed.add(path)
                self._mtimes[path] = new_snapshot

        return changed

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                changed = self._poll_once()
                if changed:
                    await self._fire_callbacks(changed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("FileWatcher poll error")

    async def _inotify_loop(self) -> None:
        import ctypes

        IN_CLOSE_WRITE = 0x00000008
        IN_DELETE = 0x00000200
        IN_MOVED_FROM = 0x00000040
        IN_MOVED_TO = 0x00000080
        IN_CREATE = 0x00000100
        MASK = IN_CLOSE_WRITE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        fd = libc.inotify_init()
        if fd < 0:
            logger.warning("inotify_init failed, falling back to polling")
            self._task = asyncio.create_task(self._poll_loop())
            return

        wd_to_path: dict[int, Path] = {}
        for path in list(self._watched.keys()):
            if not path.exists():
                continue
            b = str(path).encode()
            wd = libc.inotify_add_watch(fd, b, MASK)
            if wd >= 0:
                wd_to_path[wd] = path

        try:
            while True:
                r, _, _ = await asyncio.to_thread(
                    select.select, [fd], [], [], self._poll_interval
                )
                if not r:
                    changed = self._poll_once()
                    if changed:
                        await self._fire_callbacks(changed)
                    continue

                buf = os.read(fd, 4096)
                changed: set[Path] = set()
                i = 0
                while i + 16 <= len(buf):
                    wd = int.from_bytes(buf[i : i + 4], sys.byteorder)
                    mask = int.from_bytes(buf[i + 4 : i + 8], sys.byteorder)
                    i += 16
                    wpath = wd_to_path.get(wd)
                    if wpath and (mask & MASK):
                        changed.add(wpath)
                if changed:
                    await self._fire_callbacks(changed)
                    if changed:
                        self._seed_mtimes(changed.pop())
        except asyncio.CancelledError:
            raise
        finally:
            os.close(fd)

    async def _fire_callbacks(self, changed: set[Path]) -> None:
        for path, callbacks in self._watched.items():
            for cb in callbacks:
                try:
                    result = cb(changed)
                    if hasattr(result, "__await__"):
                        await result
                except Exception:
                    logger.exception("FileWatcher callback error for %s", path)
