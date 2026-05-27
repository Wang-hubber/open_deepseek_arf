import pytest
from arf.resources.cache import ResourceCache


class TestEmptyCache:
    def test_kernel_is_empty(self):
        cache = ResourceCache()
        assert cache.kernel == {}

    def test_dynamic_is_empty(self):
        cache = ResourceCache()
        assert cache.dynamic == {}


class TestDynamicOperations:
    def test_set_and_get(self):
        cache = ResourceCache()
        cache.dynamic["file_reader"] = {"name": "file_reader", "activation": "kernel"}
        assert "file_reader" in cache.dynamic
        assert cache.dynamic["file_reader"]["name"] == "file_reader"


class TestKernelOperations:
    def test_set_and_get(self):
        cache = ResourceCache()
        cache.kernel["web_search"] = {"name": "web_search", "activation": "kernel"}
        assert "web_search" in cache.kernel


class TestFreezeKernel:
    def test_freeze_marks_frozen(self):
        cache = ResourceCache()
        cache.kernel["a"] = {}
        cache.freeze_kernel()
        assert cache._kernel_frozen is True

    def test_frozen_rejects_setitem(self):
        cache = ResourceCache()
        cache.kernel["a"] = {}
        cache.freeze_kernel()
        with pytest.raises(RuntimeError, match="kernel.*frozen"):
            cache.kernel["b"] = {}

    def test_frozen_rejects_pop(self):
        cache = ResourceCache()
        cache.kernel["a"] = {"val": 1}
        cache.freeze_kernel()
        with pytest.raises(RuntimeError, match="kernel.*frozen"):
            cache.kernel.pop("a")

    def test_frozen_rejects_popitem(self):
        cache = ResourceCache()
        cache.kernel["a"] = {"val": 1}
        cache.freeze_kernel()
        with pytest.raises(RuntimeError, match="kernel.*frozen"):
            cache.kernel.popitem()

    def test_frozen_rejects_clear(self):
        cache = ResourceCache()
        cache.kernel["a"] = {"val": 1}
        cache.freeze_kernel()
        with pytest.raises(RuntimeError, match="kernel.*frozen"):
            cache.kernel.clear()


class TestInvalidateDynamic:
    def test_clears_dynamic_only(self):
        cache = ResourceCache()
        cache.kernel["k"] = {"name": "k"}
        cache.dynamic["d"] = {"name": "d"}
        cache.invalidate_dynamic()
        assert "k" in cache.kernel
        assert cache.dynamic == {}

    def test_does_not_touch_frozen_kernel(self):
        cache = ResourceCache()
        cache.kernel["k"] = {"name": "k"}
        cache.freeze_kernel()
        cache.dynamic["d"] = {"name": "d"}
        cache.invalidate_dynamic()
        assert cache.kernel["k"] == {"name": "k"}


class TestLookup:
    def test_has_kernel(self):
        cache = ResourceCache()
        cache.kernel["k"] = {}
        cache.dynamic["d"] = {}
        assert cache.has_kernel("k") is True
        assert cache.has_kernel("d") is False

    def test_has_dynamic(self):
        cache = ResourceCache()
        cache.kernel["k"] = {}
        cache.dynamic["d"] = {}
        assert cache.has_dynamic("d") is True
        assert cache.has_dynamic("k") is False

    def test_all_items_merges_both(self):
        cache = ResourceCache()
        cache.kernel["k"] = {"name": "k"}
        cache.dynamic["d"] = {"name": "d"}
        all_items = cache.all_items()
        assert set(all_items.keys()) == {"k", "d"}
