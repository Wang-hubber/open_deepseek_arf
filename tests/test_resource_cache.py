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
