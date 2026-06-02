"""ResourceCache — simple name→config store with invalidation support."""
from typing import Any


class ResourceCache:
    """Flat cache for framework resources. Cleared on filesystem change."""

    def __init__(self):
        self._items: dict[str, Any] = {}

    def has(self, name: str) -> bool:
        return name in self._items

    def get_all(self) -> list:
        return list(self._items.values())

    def get(self, name: str) -> Any | None:
        return self._items.get(name)

    def put(self, name: str, value: Any) -> None:
        self._items[name] = value

    def invalidate(self) -> None:
        """Clear all entries on filesystem change."""
        self._items.clear()
