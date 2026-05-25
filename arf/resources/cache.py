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
