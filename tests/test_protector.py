"""Tests for ModelCallProtector integration."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from arf.protection.protector import ModelCallProtector
from arf.protection.errors import RateLimitError, CircuitOpenError


class TestModelCallProtector:
    @pytest.fixture
    def model_map(self):
        return {
            "deep": {"base_url": "https://api.deepseek.com", "model_name": "deepseek-v4-pro"},
            "quick": {"base_url": "https://api.deepseek.com", "model_name": "deepseek-v4-flash"},
        }

    @pytest.fixture
    def protector(self, model_map):
        return ModelCallProtector(event_bus=None, model_map=model_map)

    def test_call_passes_through_when_ok(self, protector):
        raw_call = AsyncMock(return_value={"content": "ok", "tool_calls": []})
        result = asyncio.run(
            protector.call_with_protection(raw_call, [], "deep", tools=None)
        )
        assert result == {"content": "ok", "tool_calls": []}
        raw_call.assert_called_once()

    def _prime_bucket(self, protector, api_base):
        """Trigger lazy bucket creation, then drain it."""
        bucket = protector._get_rate_limiter(api_base)  # ensure created
        bucket.tokens = 0.0
        bucket.rate = 0.0
        return bucket

    def _prime_breaker(self, protector, model_name, failures=3):
        """Trigger lazy breaker creation, then trip it."""
        breaker = protector._get_breaker(model_name)  # ensure created
        for _ in range(failures):
            asyncio.run(breaker.on_failure("500"))
        return breaker

    def test_call_raises_rate_limit_error_when_bucket_empty(self, protector):
        self._prime_bucket(protector, "https://api.deepseek.com")
        raw_call = AsyncMock()
        with pytest.raises(RateLimitError):
            asyncio.run(protector.call_with_protection(raw_call, [], "deep"))

    def test_call_raises_circuit_open_error_when_breaker_open(self, protector):
        self._prime_breaker(protector, "deepseek-v4-pro", 3)
        raw_call = AsyncMock()
        with pytest.raises(CircuitOpenError):
            asyncio.run(protector.call_with_protection(raw_call, [], "deep"))

    def test_success_closes_breaker(self, protector):
        breaker = self._prime_breaker(protector, "deepseek-v4-pro", 3)
        assert breaker.state.value == "open"
        breaker.open_duration = 0.0
        breaker.last_failure_time = 0.0

        raw_call = AsyncMock(return_value={"content": "ok", "tool_calls": []})
        result = asyncio.run(protector.call_with_protection(raw_call, [], "deep"))
        assert result == {"content": "ok", "tool_calls": []}
        assert breaker.state.value == "closed"

    def test_events_emitted_on_rate_limit(self, model_map):
        event_bus = MagicMock()
        event_bus.emit = MagicMock()
        protector = ModelCallProtector(event_bus=event_bus, model_map=model_map)
        self._prime_bucket(protector, "https://api.deepseek.com")

        with pytest.raises(RateLimitError):
            asyncio.run(protector.call_with_protection(AsyncMock(), [], "deep"))

        event_bus.emit.assert_called_once()
        emitted = event_bus.emit.call_args[0][0]
        assert emitted.type == "rate_limited"
        assert emitted.data["model"] == "deepseek-v4-pro"

    def test_circuit_opened_emitted_on_failure(self, model_map):
        """circuit_opened is emitted when the breaker trips during a call."""
        event_bus = MagicMock()
        event_bus.emit = MagicMock()
        protector = ModelCallProtector(event_bus=event_bus, model_map=model_map)

        raw_call = AsyncMock(side_effect=Exception("500 error"))

        for _ in range(2):
            try:
                asyncio.run(protector.call_with_protection(raw_call, [], "deep"))
            except Exception:
                pass

        # 3rd failure should trip the circuit
        with pytest.raises(Exception):
            asyncio.run(protector.call_with_protection(raw_call, [], "deep"))

        event_types = [c[0][0].type for c in event_bus.emit.call_args_list]
        assert "circuit_opened" in event_types

    def test_breaker_blocked_emitted_on_open_circuit(self, model_map):
        """breaker_blocked is emitted when an open circuit rejects a call."""
        event_bus = MagicMock()
        event_bus.emit = MagicMock()
        protector = ModelCallProtector(event_bus=event_bus, model_map=model_map)
        self._prime_breaker(protector, "deepseek-v4-pro", 3)

        with pytest.raises(CircuitOpenError):
            asyncio.run(protector.call_with_protection(AsyncMock(), [], "deep"))

        event_types = [c[0][0].type for c in event_bus.emit.call_args_list]
        assert "breaker_blocked" in event_types

    def test_stream_model_also_protected(self, model_map):
        protector = ModelCallProtector(event_bus=None, model_map=model_map)

        async def mock_stream(messages, model_name="", tools=None):
            yield {"type": "chunk", "content": "hello"}
            yield {"type": "usage", "total_tokens": 10}

        async def collect():
            results = []
            async for chunk in protector.stream_with_protection(
                mock_stream, [], "deep"
            ):
                results.append(chunk)
            return results

        results = asyncio.run(collect())
        assert len(results) == 2
        assert results[0] == {"type": "chunk", "content": "hello"}

    def test_separate_buckets_for_different_api_bases(self):
        model_map = {
            "deep": {"base_url": "https://api.deepseek.com", "model_name": "v4-pro"},
            "openai": {"base_url": "https://api.openai.com", "model_name": "gpt-4"},
        }
        protector = ModelCallProtector(event_bus=None, model_map=model_map)
        ds_bucket = protector._get_rate_limiter("https://api.deepseek.com")
        ds_bucket.tokens = 0.0
        ds_bucket.rate = 0.0

        raw_call = AsyncMock(return_value={"content": "ok"})
        with pytest.raises(RateLimitError):
            asyncio.run(protector.call_with_protection(raw_call, [], "deep"))
        result = asyncio.run(protector.call_with_protection(raw_call, [], "openai"))
        assert result == {"content": "ok"}

    def test_unknown_model_name_falls_back(self):
        protector = ModelCallProtector(event_bus=None, model_map={})
        raw_call = AsyncMock(return_value={"content": "ok"})
        result = asyncio.run(
            protector.call_with_protection(raw_call, [], "unknown_model")
        )
        assert result == {"content": "ok"}
