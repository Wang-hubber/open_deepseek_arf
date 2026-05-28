"""Tests for CircuitBreaker state machine."""
import asyncio
import time

import pytest

from arf.protection.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(failure_threshold=3, base_cooldown=10.0)
        assert cb.state == CircuitState.CLOSED

    def test_before_call_returns_true_when_closed(self):
        cb = CircuitBreaker(failure_threshold=3, base_cooldown=10.0)
        assert asyncio.run(cb.before_call()) is True

    def test_trips_open_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, base_cooldown=10.0)
        asyncio.run(cb.on_failure("500 error"))
        assert cb.state == CircuitState.CLOSED
        asyncio.run(cb.on_failure("500 error"))
        assert cb.state == CircuitState.CLOSED
        asyncio.run(cb.on_failure("503 error"))
        assert cb.state == CircuitState.OPEN

    def test_open_rejects_requests(self):
        cb = CircuitBreaker(failure_threshold=1, base_cooldown=60.0)
        asyncio.run(cb.on_failure("500 error"))
        result = asyncio.run(cb.before_call())
        assert result is False

    def test_transitions_to_half_open_after_cooldown(self):
        cb = CircuitBreaker(failure_threshold=1, base_cooldown=0.01)
        asyncio.run(cb.on_failure("500 error"))
        assert cb.state == CircuitState.OPEN
        time.sleep(0.02)
        asyncio.run(cb.before_call())
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, base_cooldown=0.01)
        asyncio.run(cb.on_failure("500 error"))
        time.sleep(0.02)
        asyncio.run(cb.before_call())
        asyncio.run(cb.on_success())
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, base_cooldown=0.01)
        asyncio.run(cb.on_failure("500 error"))
        time.sleep(0.02)
        asyncio.run(cb.before_call())
        asyncio.run(cb.on_failure("500 error"))
        assert cb.state == CircuitState.OPEN

    def test_exponential_cooldown_growth(self):
        cb = CircuitBreaker(failure_threshold=1, base_cooldown=10.0,
                             cooldown_multiplier=2.0, max_cooldown=300.0)
        asyncio.run(cb.on_failure("error"))
        assert cb.open_duration == 10.0
        cb.state = CircuitState.HALF_OPEN
        asyncio.run(cb.on_failure("error"))
        assert cb.open_duration == 20.0
        cb.state = CircuitState.HALF_OPEN
        asyncio.run(cb.on_failure("error"))
        assert cb.open_duration == 40.0

    def test_cooldown_capped_at_max(self):
        cb = CircuitBreaker(failure_threshold=1, base_cooldown=10.0,
                             cooldown_multiplier=2.0, max_cooldown=30.0)
        asyncio.run(cb.on_failure("error"))
        assert cb.open_duration == 10.0
        cb.state = CircuitState.HALF_OPEN
        asyncio.run(cb.on_failure("error"))
        assert cb.open_duration == 20.0
        cb.state = CircuitState.HALF_OPEN
        asyncio.run(cb.on_failure("error"))
        assert cb.open_duration == 30.0
        cb.state = CircuitState.HALF_OPEN
        asyncio.run(cb.on_failure("error"))
        assert cb.open_duration == 30.0

    def test_success_in_closed_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3, base_cooldown=10.0)
        asyncio.run(cb.on_failure("error"))
        asyncio.run(cb.on_failure("error"))
        assert cb.failure_count == 2
        asyncio.run(cb.on_success())
        assert cb.failure_count == 0

    def test_negative_failure_threshold_raises(self):
        with pytest.raises(ValueError):
            CircuitBreaker(failure_threshold=0)

    def test_negative_base_cooldown_raises(self):
        with pytest.raises(ValueError):
            CircuitBreaker(failure_threshold=3, base_cooldown=0.0)
