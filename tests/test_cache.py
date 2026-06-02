"""Test ResourceCache — flat name→config store with invalidation."""
from arf.resources.cache import ResourceCache


class TestResourceCache:
    def test_put_and_get(self):
        cache = ResourceCache()
        cache.put("a", 1)
        assert cache.get("a") == 1
        assert cache.has("a")
        assert not cache.has("b")

    def test_put_overwrites(self):
        cache = ResourceCache()
        cache.put("a", 1)
        cache.put("a", 2)
        assert cache.get("a") == 2

    def test_get_missing_returns_none(self):
        cache = ResourceCache()
        assert cache.get("x") is None

    def test_get_all(self):
        cache = ResourceCache()
        cache.put("a", 1)
        cache.put("b", 2)
        assert sorted(cache.get_all()) == [1, 2]

    def test_invalidate_clears_all(self):
        cache = ResourceCache()
        cache.put("a", 1)
        cache.put("b", 2)
        cache.invalidate()
        assert cache.get_all() == []
        assert cache.get("a") is None

    def test_has_after_invalidate(self):
        cache = ResourceCache()
        cache.put("x", 42)
        cache.invalidate()
        assert not cache.has("x")
