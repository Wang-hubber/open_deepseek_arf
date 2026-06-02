"""Test ModelDegrader — ordered fallback, degradation triggers."""
import asyncio
import pytest
from arf.core.model_degrader import ModelDegrader


class FakeResponse:
    def __init__(self, content="", tool_calls=None, usage=None):
        self.content = content
        self.tool_calls = tool_calls
        self.usage = usage


class FakeAdapter:
    def __init__(self, name="fake", should_fail=False, status_code=None, error_type=None):
        self.name = name
        self.should_fail = should_fail
        self._status_code = status_code
        self._error_type = error_type
        self.calls = []

    async def chat_complete(self, messages, tools=None, max_tokens=None):
        self.calls.append({"messages": messages, "tools": tools})
        if self.should_fail:
            if self._status_code:
                class APIError(Exception):
                    pass
                e = APIError(f"HTTP {self._status_code}")
                e.http_status = self._status_code
                raise e
            raise RuntimeError(f"{self.name} failed")
        return FakeResponse(content=f"response from {self.name}")


class TestModelDegrader:
    def test_single_adapter_succeeds(self):
        adapter = FakeAdapter("pro")
        degrader = ModelDegrader([adapter])
        result = asyncio.run(degrader.chat_complete([{"role": "user", "content": "hi"}]))
        assert "pro" in result.content

    def test_falls_back_on_first_failure(self):
        bad = FakeAdapter("bad", should_fail=True)
        good = FakeAdapter("good")
        degrader = ModelDegrader([bad, good])
        result = asyncio.run(degrader.chat_complete([{"role": "user", "content": "hi"}]))
        assert "good" in result.content

    def test_raises_after_all_fail(self):
        a1 = FakeAdapter("a1", should_fail=True)
        a2 = FakeAdapter("a2", should_fail=True)
        degrader = ModelDegrader([a1, a2])
        with pytest.raises(RuntimeError, match="a2 failed"):
            asyncio.run(degrader.chat_complete([{"role": "user", "content": "hi"}]))

    def test_does_not_degrade_on_400(self):
        bad = FakeAdapter("bad", should_fail=True, status_code=400)
        good = FakeAdapter("good")
        degrader = ModelDegrader([bad, good])
        with pytest.raises(Exception):
            asyncio.run(degrader.chat_complete([{"role": "user", "content": "hi"}]))
        assert len(bad.calls) == 1
        assert len(good.calls) == 0  # 400 should NOT trigger degradation

    def test_degrades_on_500(self):
        bad = FakeAdapter("bad", should_fail=True, status_code=500)
        good = FakeAdapter("good")
        degrader = ModelDegrader([bad, good])
        result = asyncio.run(degrader.chat_complete([{"role": "user", "content": "hi"}]))
        assert "good" in result.content

    def test_degrades_on_429(self):
        bad = FakeAdapter("bad", should_fail=True, status_code=429)
        good = FakeAdapter("good")
        degrader = ModelDegrader([bad, good])
        result = asyncio.run(degrader.chat_complete([{"role": "user", "content": "hi"}]))
        assert "good" in result.content

    def test_empty_adapters_raises(self):
        with pytest.raises(ValueError, match="At least one"):
            ModelDegrader([])
