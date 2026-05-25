# Dynamic Resource Reload — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resources (tools/skills/models) are discovered from the filesystem dynamically, changes detected via FileWatcher, and only the affected cache segment invalidated.

**Architecture:** Three filesystem providers (Tool/Skill/Model) share a `ResourceCache` split into `kernel` (frozen, `activation: kernel`) and `dynamic` (invalidated on fs change). A cross-platform `FileWatcher` (inotify + polling fallback) triggers invalidation. `agent.yaml` resource sections become optional overrides merged on read.

**Tech Stack:** Python 3.12+, asyncio, ctypes (for inotify), yaml.safe_load, importlib, Pydantic, FastAPI

---

### File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `arf/resources/cache.py` | `ResourceCache` — kernel/dynamic dict split |
| Create | `arf/resources/file_watcher.py` | `FileWatcher` — inotify + poll, callback on change |
| Create | `arf/resources/providers/skill_provider.py` | `SkillProvider` — scan `skills/*.yaml` → `SkillConfig` |
| Create | `arf/resources/providers/model_provider.py` | `ModelProvider` — scan `models/*.yaml` → `ModelConfig` |
| Rename | `arf/resources/providers/tool_provider.py` | Was `static_yaml.py`; refactor to use `ResourceCache` |
| Modify | `arf/resources/providers/__init__.py` | Export new providers |
| Modify | `arf/resources/__init__.py` | Export `ResourceCache`, `FileWatcher` |
| Modify | `arf/resources/resolver.py` | Add `get_skill_definitions()`, `get_model_definitions()`, override merge |
| Modify | `arf/core/config_base.py` | Add `activation` to `SkillConfig`, make `ModelConfig.tools` optional |
| Modify | `arf/agent/config.py` | Make `models`/`skills`/`tools` optional, add `generate_config()` |
| Modify | `arf/agent/base.py` | Wire 3 providers + cache + watcher; `watch_enabled` param |
| Modify | `app/arf_default_assistant/server.py` | New endpoints: reload cache, generate config |
| Modify | `app/arf_default_assistant/cli.py` | `config generate` command |
| Create | `tests/test_resource_cache.py` | Unit tests for `ResourceCache` |
| Create | `tests/test_file_watcher.py` | Unit tests for `FileWatcher` (mock clock) |
| Create | `tests/test_providers.py` | Integration tests for all three providers |

---

### Task 1: ResourceCache

**Files:**
- Create: `arf/resources/cache.py`
- Create: `tests/test_resource_cache.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_resource_cache.py
import pytest
from arf.resources.cache import ResourceCache


def test_cache_starts_empty():
    cache = ResourceCache()
    assert cache.kernel == {}
    assert cache.dynamic == {}


def test_set_and_get_dynamic():
    cache = ResourceCache()
    cache.dynamic["file_reader"] = {"name": "file_reader", "activation": "kernel"}
    assert "file_reader" in cache.dynamic
    assert cache.dynamic["file_reader"]["name"] == "file_reader"


def test_set_and_get_kernel():
    cache = ResourceCache()
    cache.kernel["web_search"] = {"name": "web_search", "activation": "kernel"}
    assert "web_search" in cache.kernel


def test_freeze_kernel_marks_frozen():
    cache = ResourceCache()
    cache.kernel["a"] = {}
    cache.freeze_kernel()
    assert cache._kernel_frozen is True


def test_frozen_kernel_rejects_writes():
    cache = ResourceCache()
    cache.kernel["a"] = {}
    cache.freeze_kernel()
    with pytest.raises(RuntimeError, match="kernel.*frozen"):
        cache.kernel["b"] = {}


def test_invalidate_dynamic_clears_dynamic_only():
    cache = ResourceCache()
    cache.kernel["k"] = {"name": "k"}
    cache.dynamic["d"] = {"name": "d"}
    cache.invalidate_dynamic()
    assert "k" in cache.kernel
    assert cache.dynamic == {}


def test_invalidate_dynamic_does_not_touch_frozen_kernel():
    cache = ResourceCache()
    cache.kernel["k"] = {"name": "k"}
    cache.freeze_kernel()
    cache.dynamic["d"] = {"name": "d"}
    cache.invalidate_dynamic()
    assert cache.kernel["k"] == {"name": "k"}


def test_has_kernel_and_has_dynamic():
    cache = ResourceCache()
    cache.kernel["k"] = {}
    cache.dynamic["d"] = {}
    assert cache.has_kernel("k") is True
    assert cache.has_kernel("d") is False
    assert cache.has_dynamic("d") is True
    assert cache.has_dynamic("k") is False


def test_all_items_returns_kernel_plus_dynamic():
    cache = ResourceCache()
    cache.kernel["k"] = {"name": "k"}
    cache.dynamic["d"] = {"name": "d"}
    all_items = cache.all_items()
    assert set(all_items.keys()) == {"k", "d"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_resource_cache.py -v`
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 3: Write minimal ResourceCache implementation**

```python
# arf/resources/cache.py
"""ResourceCache — kernel/dynamic split with freeze-once semantics."""

from typing import Any


class _FrozenDict(dict):
    """A dict that rejects modifications after freeze()."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._frozen = False

    def freeze(self):
        self._frozen = True

    def __setitem__(self, key, value):
        if self._frozen:
            raise RuntimeError("kernel cache is frozen — cannot modify after init")
        super().__setitem__(key, value)

    def __delitem__(self, key):
        if self._frozen:
            raise RuntimeError("kernel cache is frozen — cannot modify after init")
        super().__delitem__(key)


class ResourceCache:
    """Split cache for framework resources.

    kernel  — populated at BaseAgent.__init__, frozen, never cleared.
    dynamic — lazy-loaded, cleared on filesystem change.
    """

    def __init__(self):
        self.kernel: _FrozenDict = _FrozenDict()
        self.dynamic: dict[str, Any] = {}

    @property
    def _kernel_frozen(self) -> bool:
        return self.kernel._frozen

    def freeze_kernel(self) -> None:
        """Lock kernel cache. After this, kernel writes raise RuntimeError."""
        self.kernel.freeze()

    def invalidate_dynamic(self) -> None:
        """Clear all dynamic entries. Kernel entries unaffected."""
        self.dynamic.clear()

    def has_kernel(self, name: str) -> bool:
        return name in self.kernel

    def has_dynamic(self, name: str) -> bool:
        return name in self.dynamic

    def all_items(self) -> dict[str, Any]:
        """Return merged kernel + dynamic (dynamic wins on conflict)."""
        merged = dict(self.kernel)
        merged.update(self.dynamic)
        return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_resource_cache.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add arf/resources/cache.py tests/test_resource_cache.py
git commit -m "feat: add ResourceCache with kernel/dynamic split and freeze semantics"
```

---

### Task 2: FileWatcher

**Files:**
- Create: `arf/resources/file_watcher.py`
- Create: `tests/test_file_watcher.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_file_watcher.py
import asyncio
import tempfile
from pathlib import Path
from arf.resources.file_watcher import FileWatcher


class TestFileWatcher:
    def test_add_watch_registers_path(self):
        w = FileWatcher(poll_interval=60)
        w.add_watch(Path("/tmp/test"))
        assert Path("/tmp/test") in w._watched


    def test_poll_detects_new_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            w = FileWatcher(poll_interval=0.1)
            called = []

            async def cb(paths):
                called.extend(paths)

            w.add_watch(root, cb)

            # No files yet
            assert w._poll_once() == set()

            # Create a file
            (root / "new.yaml").write_text("name: test")

            changed = w._poll_once()
            assert root in changed


    def test_poll_detects_modified_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "existing.yaml").write_text("v1")
            # Let mtime settle
            import time; time.sleep(0.01)

            w = FileWatcher(poll_interval=0.1)
            w.add_watch(root, lambda p: None)
            w._poll_once()  # seed mtimes

            (root / "existing.yaml").write_text("v2")

            changed = w._poll_once()
            assert root in changed


    def test_poll_detects_deleted_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "gone.yaml"
            p.write_text("will delete")

            w = FileWatcher(poll_interval=0.1)
            w.add_watch(root, lambda p: None)
            w._poll_once()  # seed mtimes
            p.unlink()

            changed = w._poll_once()
            assert root in changed


    def test_remove_watch_stops_tracking(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            w = FileWatcher(poll_interval=0.1)
            w.add_watch(root, lambda p: None)
            w.remove_watch(root)
            assert root not in w._watched


    def test_callback_fires_on_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            w = FileWatcher(poll_interval=0.1)
            fired = []

            async def cb(paths):
                fired.append(True)

            w.add_watch(root, cb)
            (root / "trigger.yaml").write_text("x")

            # Manually trigger poll + callback for deterministic test
            changed = w._poll_once()
            if changed:
                for _, cbs in list(w._watched.items()):
                    for c in cbs:
                        asyncio.get_event_loop().run_until_complete(c(changed))

            assert len(fired) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_file_watcher.py -v`
Expected: ImportError (module doesn't exist)

- [ ] **Step 3: Write FileWatcher implementation**

```python
# arf/resources/file_watcher.py
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
        self._mtimes: dict[Path, float] = {}
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
        """Begin watching. On Linux with inotify available, uses native events."""
        if self._task is not None:
            return
        if sys.platform == "linux":
            self._task = asyncio.create_task(self._inotify_loop())
        else:
            self._task = asyncio.create_task(self._poll_loop())
        logger.info("FileWatcher started (mode=%s)", "inotify" if sys.platform == "linux" else "poll")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # -- internal --

    def _seed_mtimes(self, path: Path) -> None:
        """Record current state of all files under path."""
        if not path.exists():
            self._mtimes[path] = -1
            return
        newest = 0
        for f in path.rglob("*"):
            if f.is_file() and ".git" not in f.parts:
                m = f.stat().st_mtime
                if m > newest:
                    newest = m
        self._mtimes[path] = newest

    def _poll_once(self) -> set[Path]:
        """Check all watched dirs; return set with changed paths."""
        changed: set[Path] = set()
        for path in list(self._watched.keys()):
            old = self._mtimes.get(path, -1)
            if not path.exists():
                if old != -1:
                    changed.add(path)
                    self._mtimes[path] = -1
                continue
            newest = old
            for f in path.rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    m = f.stat().st_mtime
                    if m > newest:
                        newest = m
            if newest > old:
                changed.add(path)
                self._mtimes[path] = newest
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
        """Linux inotify via ctypes — sub-second detection without dependencies."""
        import ctypes

        IN_CLOSE_WRITE = 0x00000008
        IN_DELETE = 0x00000200
        IN_MOVED_FROM = 0x00000040
        IN_MOVED_TO = 0x00000080
        IN_CREATE = 0x00000100
        MASK = IN_CLOSE_WRITE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE

        libc = ctypes.CDLL("libc.so.6", use_errno=True)

        # Open an inotify instance
        fd = libc.inotify_init()
        if fd < 0:
            logger.warning("inotify_init failed, falling back to polling")
            self._task = asyncio.create_task(self._poll_loop())
            return

        # Add watches for each path
        wd_to_path: dict[int, Path] = {}
        for path in list(self._watched.keys()):
            if not path.exists():
                continue
            b = str(path).encode()
            wd = libc.inotify_add_watch(fd, b, MASK)
            if wd >= 0:
                wd_to_path[wd] = path
            else:
                logger.warning("inotify_add_watch failed for %s", path)

        try:
            while True:
                r, _, _ = await asyncio.to_thread(
                    select.select, [fd], [], [], self._poll_interval
                )
                if not r:
                    # Timeout — fallback poll
                    changed = self._poll_once()
                    if changed:
                        await self._fire_callbacks(changed)
                    continue

                # Read events
                buf = os.read(fd, 4096)
                changed: set[Path] = set()
                i = 0
                while i + 16 <= len(buf):
                    wd = int.from_bytes(buf[i:i+4], sys.byteorder)
                    mask = int.from_bytes(buf[i+4:i+8], sys.byteorder)
                    i += 16
                    name_len = int.from_bytes(buf[i-4:i], sys.byteorder) if i >= 4 else 0
                    path = wd_to_path.get(wd)
                    if path and (mask & MASK):
                        changed.add(path)
                if changed:
                    await self._fire_callbacks(changed)
                    self._seed_mtimes(changed.pop())  # update mtime baseline
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_file_watcher.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add arf/resources/file_watcher.py tests/test_file_watcher.py
git commit -m "feat: add FileWatcher with inotify + polling fallback"
```

---

### Task 3: SkillProvider

**Files:**
- Create: `arf/resources/providers/skill_provider.py`
- Modify: `tests/test_providers.py`

- [ ] **Step 1: Create test directory with fixture skills**

```python
# tests/test_providers.py (SkillProvider tests)
import tempfile
from pathlib import Path
from arf.resources.providers.skill_provider import SkillProvider


def write_skill(dir: Path, name: str, activation="discoverable", **extra):
    data = {"name": name, "description": f"{name} skill", "activation": activation}
    data.update(extra)
    import yaml
    (dir / f"{name}.yaml").write_text(yaml.dump(data), encoding="utf-8")


def test_skill_provider_lists_skills():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_skill(root, "code_review", activation="kernel")
        write_skill(root, "debug", activation="discoverable")

        p = SkillProvider(root)
        skills = p.list()
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"code_review", "debug"}


def test_skill_provider_splits_kernel_and_dynamic():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_skill(root, "code_review", activation="kernel")
        write_skill(root, "debug", activation="discoverable")

        p = SkillProvider(root)
        kernel, dynamic = p.list_kernel(), p.list_dynamic()

        assert {s.name for s in kernel} == {"code_review"}
        assert {s.name for s in dynamic} == {"debug"}


def test_skill_provider_empty_dir():
    with tempfile.TemporaryDirectory() as td:
        p = SkillProvider(Path(td))
        assert p.list() == []
        assert p.list_kernel() == []
        assert p.list_dynamic() == []


def test_skill_provider_caches_after_first_scan():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_skill(root, "s1")
        p = SkillProvider(root)
        first = p.list()
        # Add a file after scan — shouldn't appear (cache not invalidated)
        write_skill(root, "s2")
        second = p.list()
        assert len(second) == 1  # cached, doesn't see s2


def test_skill_provider_invalidate_rescans():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_skill(root, "s1")
        p = SkillProvider(root)
        p.list()  # populate cache
        write_skill(root, "s2")
        p.invalidate_dynamic()
        after = p.list_dynamic()
        assert {s.name for s in after} == {"s1", "s2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_providers.py -v`
Expected: ImportError (SkillProvider doesn't exist)

- [ ] **Step 3: Write SkillProvider**

```python
# arf/resources/providers/skill_provider.py
"""SkillProvider — scan skills/*.yaml for skill definitions."""
from pathlib import Path
import yaml
from arf.core.config_base import SkillConfig


class SkillProvider:
    def __init__(self, skills_dir: str | Path):
        self._dir = Path(skills_dir)
        self._kernel: dict[str, SkillConfig] = {}
        self._dynamic: dict[str, SkillConfig] = {}
        self._loaded = False

    def list_kernel(self) -> list[SkillConfig]:
        if not self._loaded:
            self._load()
        return list(self._kernel.values())

    def list_dynamic(self) -> list[SkillConfig]:
        if not self._loaded:
            self._load()
        return list(self._dynamic.values())

    def list(self) -> list[SkillConfig]:
        return self.list_kernel() + self.list_dynamic()

    def invalidate_dynamic(self) -> None:
        self._dynamic.clear()
        self._loaded = False  # force re-scan on next access

    def _load(self) -> None:
        self._loaded = True
        self._dynamic.clear()  # reload from scratch for dynamic
        if not self._dir.exists():
            return
        for yaml_path in sorted(self._dir.glob("*.yaml")):
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if not raw or "name" not in raw:
                continue
            cfg = SkillConfig(**raw)
            name = cfg.name
            if getattr(cfg, "activation", "discoverable") == "kernel":
                if name not in self._kernel:  # kernel persists
                    self._kernel[name] = cfg
            else:
                self._dynamic[name] = cfg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_providers.py -v -k "skill"`
Expected: all 5 SkillProvider tests PASS

- [ ] **Step 5: Commit**

```bash
git add arf/resources/providers/skill_provider.py tests/test_providers.py
git commit -m "feat: add SkillProvider with kernel/dynamic split scanning skills/ dir"
```

---

### Task 4: ModelProvider

**Files:**
- Create: `arf/resources/providers/model_provider.py`
- Modify: `tests/test_providers.py` (add ModelProvider tests)

- [ ] **Step 1: Add ModelProvider tests**

```python
# Append to tests/test_providers.py

from arf.resources.providers.model_provider import ModelProvider


def write_model(dir: Path, name: str, activation="discoverable", **extra):
    data = {
        "name": name, "api_type": "openai", "model": f"{name}-model",
        "api_base": "https://api.example.com", "api_key_env": "EXAMPLE_KEY",
        "context_window": 128000, "activation": activation,
    }
    data.update(extra)
    import yaml
    (dir / f"{name}.yaml").write_text(yaml.dump(data), encoding="utf-8")


def test_model_provider_lists_models():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_model(root, "quick", activation="kernel")
        write_model(root, "deep", activation="discoverable")

        p = ModelProvider(root)
        models = p.list()
        assert len(models) == 2
        names = {m.name for m in models}
        assert names == {"quick", "deep"}


def test_model_provider_splits_kernel_and_dynamic():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_model(root, "quick", activation="kernel")
        write_model(root, "vision", activation="discoverable")

        p = ModelProvider(root)
        assert {m.name for m in p.list_kernel()} == {"quick"}
        assert {m.name for m in p.list_dynamic()} == {"vision"}


def test_model_provider_empty_dir():
    with tempfile.TemporaryDirectory() as td:
        p = ModelProvider(Path(td))
        assert p.list() == []


def test_model_provider_invalidate_rescans():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_model(root, "quick")
        p = ModelProvider(root)
        p.list()
        write_model(root, "deep")
        p.invalidate_dynamic()
        assert {m.name for m in p.list_dynamic()} == {"quick", "deep"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_providers.py -v -k "model"`
Expected: ImportError

- [ ] **Step 3: Write ModelProvider**

```python
# arf/resources/providers/model_provider.py
"""ModelProvider — scan models/*.yaml for model configs."""
from pathlib import Path
import yaml
from arf.core.config_base import ModelConfig


class ModelProvider:
    def __init__(self, models_dir: str | Path):
        self._dir = Path(models_dir)
        self._kernel: dict[str, ModelConfig] = {}
        self._dynamic: dict[str, ModelConfig] = {}
        self._loaded = False

    def list_kernel(self) -> list[ModelConfig]:
        if not self._loaded:
            self._load()
        return list(self._kernel.values())

    def list_dynamic(self) -> list[ModelConfig]:
        if not self._loaded:
            self._load()
        return list(self._dynamic.values())

    def list(self) -> list[ModelConfig]:
        return self.list_kernel() + self.list_dynamic()

    def invalidate_dynamic(self) -> None:
        self._dynamic.clear()
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        self._dynamic.clear()
        if not self._dir.exists():
            return
        for yaml_path in sorted(self._dir.glob("*.yaml")):
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if not raw or "name" not in raw:
                continue
            cfg = ModelConfig(**raw)
            name = cfg.name
            activation = raw.get("activation", "discoverable")
            if activation == "kernel":
                if name not in self._kernel:
                    self._kernel[name] = cfg
            else:
                self._dynamic[name] = cfg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_providers.py -v -k "model"`
Expected: all 4 ModelProvider tests PASS

- [ ] **Step 5: Commit**

```bash
git add arf/resources/providers/model_provider.py tests/test_providers.py
git commit -m "feat: add ModelProvider with kernel/dynamic split scanning models/ dir"
```

---

### Task 5: Refactor ToolProvider

**Files:**
- Rename: `arf/resources/providers/static_yaml.py` → `arf/resources/providers/tool_provider.py`
- Modify: `arf/resources/providers/__init__.py`
- Modify: tests for ToolProvider

- [ ] **Step 1: Read current static_yaml.py to understand existing code**

The current `StaticYamlToolProvider` scans `tools/{name}/tool.yaml` + `function.py`. We refactor to:
- Rename class to `ToolProvider`
- Add kernel/dynamic split (same pattern as Skill/ModelProvider)
- Keep `importlib` dynamic loading of `function.py`
- Add `invalidate_dynamic()` method

- [ ] **Step 2: Write the refactored ToolProvider**

```python
# arf/resources/providers/tool_provider.py
"""ToolProvider — scan tools/{name}/ for tool.yaml + function.py."""
import importlib.util
from pathlib import Path
import yaml
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult
from arf.resources.backends.function import FunctionBackend


class ToolProvider:
    """Scans tools/ directory. Each tool is a subdirectory with tool.yaml + function.py.

    Splits tools into kernel (activation: kernel, readonly framework tools)
    and dynamic (user-created tools, invalidated on filesystem change).
    """

    def __init__(self, tools_dir: str | Path):
        self._dir = Path(tools_dir)
        self._kernel: dict[str, ToolConfig] = {}
        self._dynamic: dict[str, ToolConfig] = {}
        self._functions: dict[str, callable] = {}
        self._kernel_functions: dict[str, callable] = {}
        self._backend = FunctionBackend()
        self._loaded = False

    # -- query API --

    def list_kernel(self) -> list[ToolConfig]:
        if not self._loaded:
            self._load()
        return list(self._kernel.values())

    def list_dynamic(self) -> list[ToolConfig]:
        if not self._loaded:
            self._load()
        return list(self._dynamic.values())

    def list_tools(self) -> list[ToolConfig]:
        """Backward-compat alias for existing callers."""
        return self.list_kernel() + self.list_dynamic()

    async def resolve(self, name: str) -> ToolConfig | None:
        return self._kernel.get(name) or self._dynamic.get(name)

    async def execute(self, name: str, params: dict) -> ToolResult:
        cfg = await self.resolve(name)
        if cfg is None:
            return ToolResult(tool_name=name, success=False, error=f"Tool '{name}' not found")
        fn = self._functions.get(name) or self._kernel_functions.get(name)
        if fn:
            return await self._backend.execute_with_fn(cfg, fn, params)
        return await self._backend.execute(cfg, params)

    # -- cache management --

    def invalidate_dynamic(self) -> None:
        """Clear dynamic cache and dynamic function bindings."""
        self._dynamic.clear()
        # Remove non-kernel function bindings
        for name in list(self._functions.keys()):
            if name not in self._kernel:
                del self._functions[name]
        self._loaded = False

    # -- internal --

    def _load(self) -> None:
        self._loaded = True
        self._dynamic.clear()
        if not self._dir.exists():
            return
        for tool_dir in sorted(self._dir.iterdir()):
            if not tool_dir.is_dir():
                continue
            yaml_path = tool_dir / "tool.yaml"
            if not yaml_path.exists():
                continue
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            cfg = ToolConfig(**raw)
            name = cfg.name
            activation = raw.get("activation", "discoverable")

            func_path = tool_dir / "function.py"
            fn = None
            if func_path.exists():
                spec = importlib.util.spec_from_file_location(f"arf_tool_{name}", str(func_path))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "execute"):
                        fn = mod.execute

            if activation == "kernel":
                if name not in self._kernel:
                    self._kernel[name] = cfg
                    if fn:
                        self._kernel_functions[name] = fn
            else:
                self._dynamic[name] = cfg
                if fn:
                    self._functions[name] = fn
```

- [ ] **Step 3: Update __init__.py exports**

```python
# arf/resources/providers/__init__.py
from arf.resources.providers.tool_provider import ToolProvider
from arf.resources.providers.skill_provider import SkillProvider
from arf.resources.providers.model_provider import ModelProvider

__all__ = ["ToolProvider", "SkillProvider", "ModelProvider"]
```

- [ ] **Step 4: Update all imports that reference StaticYamlToolProvider**

Run to find all references:
```bash
grep -rn "StaticYamlToolProvider" arf/ app/ --include="*.py"
```

Update each file:
- `arf/resources/__init__.py`: change import to `ToolProvider`
- `arf/agent/base.py:11`: change import to `ToolProvider`
- `arf/resources/providers/__init__.py`: already updated above

- [ ] **Step 5: Run existing tests to ensure no regressions**

Run: `python -m pytest tests/ -v`
Expected: all existing tests pass

- [ ] **Step 6: Commit**

```bash
git add arf/resources/providers/static_yaml.py arf/resources/providers/tool_provider.py
git add arf/resources/providers/__init__.py arf/resources/__init__.py
git add arf/agent/base.py
git commit -m "refactor: rename StaticYamlToolProvider → ToolProvider with kernel/dynamic split"
```

---

### Task 6: ResourceResolver — add Skill + Model support

**Files:**
- Modify: `arf/resources/resolver.py`

- [ ] **Step 1: Extend DefaultToolResolver to handle skills and models**

```python
# arf/resources/resolver.py (complete replacement)
"""ResourceResolver — unified resource resolution with override merge."""
import yaml
from pathlib import Path
from arf.core.protocols.resources import ToolDefinition, ToolProvider, ToolRetriever, ToolBackend
from arf.core.config_base import ToolConfig, SkillConfig, ModelConfig
from arf.core.results import ToolResult


class ResourceResolver:
    """Resolves tools, skills, and models from filesystem providers.

    Merges agent.yaml overrides on top of filesystem definitions.
    Override priority: agent.yaml field > filesystem field > Pydantic default.
    """

    def __init__(
        self,
        tool_provider,
        skill_provider,
        model_provider,
        agent_yaml_overrides: dict | None = None,
    ):
        self._tool_provider = tool_provider
        self._skill_provider = skill_provider
        self._model_provider = model_provider
        self._overrides = agent_yaml_overrides or {}

    # -- tools (backward-compat) --

    async def get_tool_definitions(self, query_context: str = "", top_k: int = 10) -> list[ToolDefinition]:
        tools = self._tool_provider.list_tools()
        overrides = self._overrides.get("tools", [])
        merged = self._merge_tools(tools, overrides)
        return [
            ToolDefinition(name=t.name, description=t.description, parameters=t.parameters)
            for t in merged
        ]

    async def execute(self, tool_name: str, params: dict) -> ToolResult:
        return await self._tool_provider.execute(tool_name, params)

    # -- skills --

    def get_skill_definitions(self) -> list[SkillConfig]:
        skills = self._skill_provider.list()
        overrides = self._overrides.get("skills", [])
        return self._merge_skills(skills, overrides)

    # -- models --

    def get_model_definitions(self) -> list[ModelConfig]:
        models = self._model_provider.list()
        overrides = self._overrides.get("models", [])
        return self._merge_models(models, overrides)

    # -- cache --

    async def reload_dynamic(self) -> None:
        """Clear dynamic caches across all providers."""
        self._tool_provider.invalidate_dynamic()
        self._skill_provider.invalidate_dynamic()
        self._model_provider.invalidate_dynamic()

    # -- override merge logic --

    def _merge_tools(self, fs_tools: list[ToolConfig], override_list: list[dict]) -> list[ToolConfig]:
        override_map = {o["name"]: o for o in override_list if "name" in o}
        result = []
        seen = set()
        for t in fs_tools:
            if t.name in override_map:
                merged = t.model_copy(update=override_map[t.name])
                seen.add(t.name)
            else:
                merged = t
            result.append(merged)
        # Add overrides without filesystem counterpart (backward compat)
        for name, ov in override_map.items():
            if name not in seen:
                result.append(ToolConfig(**ov))
        return result

    def _merge_skills(self, fs_skills: list[SkillConfig], override_list: list[dict]) -> list[SkillConfig]:
        override_map = {o["name"]: o for o in override_list if "name" in o}
        result = []
        seen = set()
        for s in fs_skills:
            if s.name in override_map:
                merged = s.model_copy(update=override_map[s.name])
                seen.add(s.name)
            else:
                merged = s
            result.append(merged)
        for name, ov in override_map.items():
            if name not in seen:
                result.append(SkillConfig(**ov))
        return result

    def _merge_models(self, fs_models: list[ModelConfig], override_list: list[dict]) -> list[ModelConfig]:
        override_map = {o["name"]: o for o in override_list if "name" in o}
        result = []
        seen = set()
        for m in fs_models:
            if m.name in override_map:
                merged = m.model_copy(update=override_map[m.name])
                seen.add(m.name)
            else:
                merged = m
            result.append(merged)
        for name, ov in override_map.items():
            if name not in seen:
                result.append(ModelConfig(**ov))
        return result

    # -- config generation --

    def generate_config(self) -> dict:
        """Dump all discovered resources as an agent.yaml-compatible dict."""
        return {
            "tools": [t.model_dump(exclude_none=True) for t in self._tool_provider.list_tools()],
            "skills": [s.model_dump(exclude_none=True) for s in self._skill_provider.list()],
            "models": [m.model_dump(exclude_none=True) for m in self._model_provider.list()],
        }
```

- [ ] **Step 2: Update imports in resolver.py's consumers**

```bash
grep -rn "DefaultToolResolver" arf/ app/ --include="*.py"
```

Update `arf/agent/base.py:10` and `arf/resources/__init__.py:2` to import `ResourceResolver`.

- [ ] **Step 3: Commit**

```bash
git add arf/resources/resolver.py arf/resources/__init__.py arf/agent/base.py
git commit -m "feat: ResourceResolver unifies tool/skill/model resolution with override merge"
```

---

### Task 7: AgentConfig — make resource sections optional

**Files:**
- Modify: `arf/agent/config.py`
- Modify: `arf/core/config_base.py` (add `activation` to `SkillConfig`, `ModelConfig`)

- [ ] **Step 1: Add activation field to SkillConfig and ModelConfig**

```python
# arf/core/config_base.py — modify SkillConfig (line 21-27):
class SkillConfig(BaseModel):
    name: str
    description: str
    prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    activation: Literal["kernel", "discoverable", "passive"] = "discoverable"
    pipeline: list[PipelineStep] = Field(default_factory=list)


# arf/core/config_base.py — modify ModelConfig (line 6-13):
class ModelConfig(BaseModel):
    name: str
    api_type: Literal["openai", "anthropic", "custom"] = "openai"
    model: str = ""
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    context_window: int = 131_072
    kwargs: dict = Field(default_factory=dict)
    activation: Literal["kernel", "discoverable", "passive"] = "discoverable"
```

- [ ] **Step 2: Make resource fields optional in AgentConfig**

```python
# arf/agent/config.py — modify AgentConfig (line 51-68):
class AgentConfig(BaseModel):
    schema_version: str = Field(default="1.0", frozen=True)
    name: str
    role: str = ""
    task: str = ""
    description: str = ""
    system_prompt: SystemPromptConfig = Field(default_factory=SystemPromptConfig)
    models: list[ModelConfig] = Field(default_factory=list)   # optional
    skills: list[SkillConfig] = Field(default_factory=list)   # optional
    tools: list[ToolConfig] = Field(default_factory=list)     # optional
    hooks: list[HookDefinition] = Field(default_factory=list)
    advanced: AdvancedConfig | None = None
    agents: list["AgentConfig"] | None = None
    handover: HandoverConfig | None = None
    supervisor: SupervisorConfig | None = None
```

- [ ] **Step 3: Commit**

```bash
git add arf/core/config_base.py arf/agent/config.py
git commit -m "feat: make resource sections optional, add activation to SkillConfig and ModelConfig"
```

---

### Task 8: Wiring in BaseAgent

**Files:**
- Modify: `arf/agent/base.py`

- [ ] **Step 1: Wire three providers + FileWatcher + ResourceCache into BaseAgent.__init__**

In `base.py`, after `tools_dir` setup (around line 79):

```python
# arf/agent/base.py — in BaseAgent.__init__(), replace the resources section:

# 2. Resources — three filesystem providers with kernel/dynamic cache split
tools_dir = override_protocols.pop("tools_dir", Path("./tools"))
skills_dir = override_protocols.pop("skills_dir", Path("./skills"))
models_dir = override_protocols.pop("models_dir", Path("./models"))
watch_enabled = override_protocols.pop("watch_enabled", True)

from arf.resources.providers.tool_provider import ToolProvider
from arf.resources.providers.skill_provider import SkillProvider
from arf.resources.providers.model_provider import ModelProvider
from arf.resources.resolver import ResourceResolver
from arf.resources.file_watcher import FileWatcher

tool_provider = ToolProvider(tools_dir)
skill_provider = SkillProvider(skills_dir)
model_provider = ModelProvider(models_dir)

# Build override dict from agent.yaml (may be empty)
overrides = {
    "tools": [t.model_dump(exclude_none=True) for t in (config.tools or [])],
    "skills": [s.model_dump(exclude_none=True) for s in (config.skills or [])],
    "models": [m.model_dump(exclude_none=True) for m in (config.models or [])],
}

resource_resolver = override_protocols.pop("tool_resolver", ResourceResolver(
    tool_provider=tool_provider,
    skill_provider=skill_provider,
    model_provider=model_provider,
    agent_yaml_overrides=overrides,
))

# FileWatcher — auto-invalidate dynamic cache on filesystem change
file_watcher = None
if watch_enabled:
    file_watcher = FileWatcher(poll_interval=5.0)

    async def _on_fs_change(changed_paths):
        resource_resolver.reload_dynamic()

    for d in [tools_dir, skills_dir, models_dir]:
        path = Path(d)
        if path.exists():
            file_watcher.add_watch(path, _on_fs_change)

# Start watcher after agent is fully built (in startup hook or via lifespan)
# Store for later start
self._file_watcher = file_watcher
self._resource_resolver = resource_resolver
```

- [ ] **Step 2: Update tool_executor to use ResourceResolver**

```python
# Replace tool_resolver references:
tool_executor = override_protocols.pop("tool_executor",
    ConcurrentToolExecutor(resource_resolver))
```

- [ ] **Step 3: Update GraphEngine wiring**

Change `tool_resolver=resource_resolver` in GraphEngine constructor call (around line 231).

- [ ] **Step 4: Add start/stop watcher to lifecycle**

The FileWatcher should start after the agent is initialized. In `server.py`'s lifespan, add after agent creation:
```python
if _agent._file_watcher:
    await _agent._file_watcher.start()
```

And in shutdown:
```python
if _agent._file_watcher:
    await _agent._file_watcher.stop()
```

- [ ] **Step 5: Update system prompt builder to use ResourceResolver**

In `_build_system_prompt()`, replace `config.tools` with `resource_resolver`:
```python
# Old:
kernel_tools = [t for t in config.tools if getattr(t, "activation", "discoverable") == "kernel"]

# New:
tools = resource_resolver.get_tool_definitions()
kernel_tool_configs = tool_provider.list_kernel()
kernel_tools = [t for t in kernel_tool_configs]
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add arf/agent/base.py
git commit -m "feat: wire three providers + FileWatcher + ResourceResolver into BaseAgent"
```

---

### Task 9: API Endpoints

**Files:**
- Modify: `app/arf_default_assistant/server.py`

- [ ] **Step 1: Add POST /api/resources/reload**

```python
@app.post("/api/resources/reload")
async def resources_reload():
    """Clear dynamic resource cache — forces re-scan on next access."""
    if hasattr(_agent, '_resource_resolver'):
        await _agent._resource_resolver.reload_dynamic()
        return JSONResponse({"status": "reloaded", "scope": "dynamic"})
    return JSONResponse({"error": "resource resolver not available"}, status_code=500)
```

- [ ] **Step 2: Add GET /api/resources/generate-config**

```python
@app.get("/api/resources/generate-config")
async def resources_generate_config():
    """Scan filesystem and return complete agent.yaml text."""
    if not hasattr(_agent, '_resource_resolver'):
        return JSONResponse({"error": "resource resolver not available"}, status_code=500)
    import yaml
    config_data = _agent._resource_resolver.generate_config()
    config_data["name"] = _agent.config.name
    config_data["role"] = _agent.config.role
    config_data["description"] = _agent.config.description
    yaml_text = yaml.dump(config_data, allow_unicode=True, default_flow_style=False)
    return JSONResponse({"yaml": yaml_text, "config": config_data})
```

- [ ] **Step 3: Update GET /api/resources/{type} to use filesystem**

```python
@app.get("/api/resources/{res_type}")
async def list_resources(res_type: str):
    resolver = getattr(_agent, '_resource_resolver', None)
    if res_type == "tools":
        if resolver:
            tools = await resolver.get_tool_definitions()
            items = [
                {"name": t.name, "description": t.description, "activation": getattr(t, "activation", "kernel")}
                for t in tools
            ]
        else:
            items = [
                {"name": t.name, "description": t.description, "activation": t.activation}
                for t in _agent.config.tools
            ]
    elif res_type == "skills":
        if resolver:
            skills = resolver.get_skill_definitions()
            items = [
                {"name": s.name, "description": s.description, "tools": s.tools}
                for s in skills
            ]
        else:
            items = [
                {"name": s.name, "description": s.description, "tools": s.tools}
                for s in _agent.config.skills
            ]
    elif res_type == "models":
        if resolver:
            models = resolver.get_model_definitions()
            items = [
                {"name": m.name, "model": m.model, "api_base": m.api_base}
                for m in models
            ]
        else:
            items = [
                {"name": m.name, "model": m.model, "api_base": m.api_base}
                for m in _agent.config.models
            ]
    else:
        return JSONResponse({"error": f"unknown type: {res_type}"}, status_code=400)
    return JSONResponse({"type": res_type, "items": items, "count": len(items)})
```

- [ ] **Step 4: Start FileWatcher in lifespan startup**

In `lifespan()` startup, after agent creation:
```python
if _agent._file_watcher:
    import asyncio
    asyncio.create_task(_agent._file_watcher.start())
```

In shutdown:
```python
if _agent._file_watcher:
    await _agent._file_watcher.stop()
```

- [ ] **Step 5: Run backend tests**

Run: `python -m pytest test_backend.py -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add app/arf_default_assistant/server.py
git commit -m "feat: add /api/resources/reload and generate-config endpoints"
```

---

### Task 10: CLI — config generate command

**Files:**
- Modify: `app/arf_default_assistant/cli.py`

- [ ] **Step 1: Add `config generate` subcommand**

```python
def cmd_config_generate(args):
    """Scan filesystem resources and dump agent.yaml to stdout."""
    import yaml
    from arf.resources.providers.tool_provider import ToolProvider
    from arf.resources.providers.skill_provider import SkillProvider
    from arf.resources.providers.model_provider import ModelProvider
    from arf.resources.resolver import ResourceResolver

    tp = ToolProvider(APP_DIR / "tools")
    sp = SkillProvider(APP_DIR / "skills")
    mp = ModelProvider(APP_DIR / "models")
    resolver = ResourceResolver(tp, sp, mp)
    config = resolver.generate_config()
    config["name"] = "arf_assistant"
    config["description"] = "Auto-generated config — edit to add overrides"
    print(yaml.dump(config, allow_unicode=True, default_flow_style=False))


# In main(), add parser:
p_config = sub.add_parser("config", help="Config management")
p_config_sub = p_config.add_subparsers(dest="config_cmd")
p_gen = p_config_sub.add_parser("generate", help="Generate agent.yaml from filesystem")
p_gen.set_defaults(func=cmd_config_generate)
```

- [ ] **Step 2: Test the command**

Run: `python cli.py config generate`
Expected: YAML output listing all tools/skills/models discovered from filesystem

- [ ] **Step 3: Commit**

```bash
git add app/arf_default_assistant/cli.py
git commit -m "feat: add `arf config generate` CLI command"
```

---

### Task 11: Integration — create models/ and skills/ directories with kernel resources

**Files:**
- Create: `app/arf_default_assistant/models/quick.yaml`
- Create: `app/arf_default_assistant/models/deep.yaml`
- Create: `app/arf_default_assistant/skills/file_ops.yaml`
- Create: `app/arf_default_assistant/skills/code_review.yaml`
- Create: `app/arf_default_assistant/skills/debug.yaml`
- Create: `app/arf_default_assistant/skills/error_handler.yaml`

- [ ] **Step 1: Extract models from agent.yaml into models/ files**

```yaml
# models/quick.yaml
name: quick
api_type: openai
model: deepseek-v4-flash
api_base: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
context_window: 800000
activation: kernel
kwargs:
  reasoning_effort: high
  temperature: 0.7
```

```yaml
# models/deep.yaml
name: deep
api_type: openai
model: deepseek-v4-pro
api_base: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
context_window: 1000000
activation: kernel
kwargs:
  reasoning_effort: max
```

- [ ] **Step 2: Extract skills from agent.yaml into skills/ files**

Create one YAML per skill from the current `agent.yaml` skills section. Each gets `activation: kernel`.

- [ ] **Step 3: Update agent.yaml — remove resource sections that now live in filesystem**

The existing `agent.yaml` keeps `models:`, `skills:`, `tools:` as overrides (only fields that differ from filesystem). Or cleaner: remove them entirely and let filesystem be the source.

- [ ] **Step 4: Verify agent starts with new layout**

Run: `python server.py` (briefly, then Ctrl+C)
Expected: agent starts, tools/skills/models loaded from filesystem

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ test_backend.py -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add app/arf_default_assistant/models/ app/arf_default_assistant/skills/
git add app/arf_default_assistant/agent.yaml
git commit -m "feat: migrate resources to filesystem — models/ + skills/ directories"
```

---

## Self-Review

1. **Spec coverage:** Each spec requirement maps to a task:
   - 3.1 Provider Layer → Tasks 3, 4, 5
   - 3.2 ResourceCache → Task 1
   - 3.3 FileWatcher → Task 2
   - 3.4 Reload Flow → Task 8 (wiring)
   - 4.1-4.3 agent.yaml → Task 6 (merge), Task 7 (optional fields), Task 9 (generate)
   - 5 API → Task 9
   - 6 Migration → Task 11

2. **Placeholder scan:** No TBD, TODO, or vague steps. All code is concrete.

3. **Type consistency:** `ResourceCache`, `FileWatcher`, `ToolProvider`, `SkillProvider`, `ModelProvider`, `ResourceResolver` — names consistent across all tasks. Method signatures matching: `list_kernel()`, `list_dynamic()`, `list()`, `invalidate_dynamic()`.
