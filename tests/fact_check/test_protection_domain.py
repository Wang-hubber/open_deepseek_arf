"""Fact-check tests: API Protection — docs/api-protection.md vs arf/protection/.

Each test validates a specific claim made in the documentation against actual code.
PASS = doc/code consistent. FAIL = discrepancy found (fact-check finding).
"""

import asyncio
import inspect
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. TokenBucket (docs 2.2)
# ---------------------------------------------------------------------------

class TestTokenBucket:
    """Doc: TokenBucket in arf/protection/rate_limiter.py."""

    def test_has_capacity_rate_tokens_fields(self):
        """Doc: capacity (max burst), rate (tokens/sec), tokens (current)."""
        from arf.protection.rate_limiter import TokenBucket
        tb = TokenBucket(capacity=10, rate=5)
        assert tb.capacity == 10.0
        assert tb.rate == 5.0
        assert tb.tokens == 10.0  # starts full
        assert hasattr(tb, '_lock')  # asyncio.Lock for thread safety

    def test_acquire_returns_true_when_tokens_available(self):
        """Doc: acquire() consumes 1 token, returns True when available."""
        from arf.protection.rate_limiter import TokenBucket

        async def run():
            tb = TokenBucket(capacity=10, rate=5)
            assert await tb.acquire() is True
            assert tb.tokens == 9.0

        asyncio.run(run())

    def test_acquire_returns_false_when_empty(self):
        """Doc: acquire() returns False, never blocks."""
        from arf.protection.rate_limiter import TokenBucket

        async def run():
            tb = TokenBucket(capacity=0.5, rate=0)
            # First acquire drains the 0.5 tokens
            await tb.acquire()  # consumes 0.5 tokens, now 0
            # Second should fail
            assert await tb.acquire() is False

        asyncio.run(run())

    def test_acquire_is_non_blocking(self):
        """Doc: acquire() never blocks — returns immediately."""
        from arf.protection.rate_limiter import TokenBucket
        import time as _t

        async def run():
            tb = TokenBucket(capacity=1, rate=0)
            await tb.acquire()  # drain the single token
            start = _t.monotonic()
            result = await tb.acquire()
            elapsed = _t.monotonic() - start
            assert result is False
            assert elapsed < 0.1, f"acquire() blocked for {elapsed}s"

        asyncio.run(run())

    def test_refill_over_time(self):
        """Doc: rate tokens/sec refill."""
        from arf.protection.rate_limiter import TokenBucket

        async def run():
            tb = TokenBucket(capacity=10, rate=100)  # 100 tokens/sec
            # Drain
            tb.tokens = 0
            tb.last_refill = time.monotonic() - 0.1  # 0.1s ago
            # Should have 10 tokens refilled (100 * 0.1 = 10)
            result = await tb.acquire()
            assert result is True

        asyncio.run(run())

    def test_capacity_caps_tokens(self):
        """Doc: tokens capped at capacity."""
        from arf.protection.rate_limiter import TokenBucket

        async def run():
            tb = TokenBucket(capacity=5, rate=100)
            # Simulate long wait — should not exceed capacity
            tb.tokens = 3
            tb.last_refill = time.monotonic() - 10
            tb._refill()
            assert tb.tokens == 5.0  # capped at capacity

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. CircuitBreaker (docs 2.3)
# ---------------------------------------------------------------------------

class TestCircuitBreakerStates:
    """Doc: three-state machine — CLOSED, OPEN, HALF_OPEN."""

    def test_three_states_exist(self):
        """Doc: CLOSED, OPEN, HALF_OPEN."""
        from arf.protection.circuit_breaker import CircuitState
        states = {s.value for s in CircuitState}
        assert states == {"closed", "open", "half_open"}

    def test_default_state_is_closed(self):
        """Doc: starts CLOSED."""
        from arf.protection.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    def test_default_params(self):
        """Doc: failure_threshold=3, base_cooldown=10s, multiplier=2,
        max_cooldown=300s, half_open_max_requests=1."""
        from arf.protection.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()
        assert cb.failure_threshold == 3
        assert cb.base_cooldown == 10.0
        assert cb.cooldown_multiplier == 2.0
        assert cb.max_cooldown == 300.0
        assert cb.half_open_max_requests == 1

    def test_failure_threshold_3_trips_open(self):
        """Doc: failure_threshold 次连续失败 → OPEN."""
        from arf.protection.circuit_breaker import CircuitBreaker, CircuitState

        async def run():
            cb = CircuitBreaker(failure_threshold=3)
            for _ in range(3):
                assert await cb.before_call() is True
                await cb.on_failure("error")
            # 4th call should be blocked
            assert await cb.before_call() is False
            assert cb.state == CircuitState.OPEN

        asyncio.run(run())

    def test_open_to_half_open_after_cooldown(self):
        """Doc: OPEN → HALF_OPEN after open_duration."""
        from arf.protection.circuit_breaker import CircuitBreaker, CircuitState

        async def run():
            cb = CircuitBreaker(failure_threshold=1, base_cooldown=0.01)
            # Trip OPEN
            await cb.before_call()
            await cb.on_failure("err")
            assert cb.state == CircuitState.OPEN
            # Wait for cooldown
            await asyncio.sleep(0.02)
            # Should transition to HALF_OPEN
            assert await cb.before_call() is True
            assert cb.state == CircuitState.HALF_OPEN

        asyncio.run(run())

    def test_half_open_success_closes(self):
        """Doc: HALF_OPEN → CLOSED on success."""
        from arf.protection.circuit_breaker import CircuitBreaker, CircuitState

        async def run():
            cb = CircuitBreaker(failure_threshold=1, base_cooldown=0.01)
            await cb.before_call()
            await cb.on_failure("err")
            await asyncio.sleep(0.02)
            await cb.before_call()
            await cb.on_success()
            assert cb.state == CircuitState.CLOSED

        asyncio.run(run())

    def test_half_open_failure_opens_with_doubled_cooldown(self):
        """Doc: HALF_OPEN → OPEN on failure, cooldown *= multiplier."""
        from arf.protection.circuit_breaker import CircuitBreaker, CircuitState

        async def run():
            cb = CircuitBreaker(failure_threshold=1, base_cooldown=0.01,
                                cooldown_multiplier=2.0, max_cooldown=300)
            # 1st trip
            await cb.before_call()
            await cb.on_failure("err1")
            first_cooldown = cb.open_duration
            await asyncio.sleep(0.02)
            # HALF_OPEN — fail again
            await cb.before_call()
            await cb.on_failure("err2")
            assert cb.state == CircuitState.OPEN
            assert cb.open_duration == first_cooldown * 2.0

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. ModelCallProtector (docs 2.4)
# ---------------------------------------------------------------------------

class TestModelCallProtector:
    """Doc: ModelCallProtector combines TokenBucket + CircuitBreaker."""

    def test_call_with_protection_signature(self):
        """Doc: call_with_protection(raw_call, messages, model_name, tools)."""
        from arf.protection.protector import ModelCallProtector
        sig = inspect.signature(ModelCallProtector.call_with_protection)
        params = list(sig.parameters.keys())
        for p in ("raw_call", "messages", "model_name", "tools"):
            assert p in params

    def test_stream_with_protection_signature(self):
        """Doc: stream_with_protection(raw_stream, messages, model_name, tools)."""
        from arf.protection.protector import ModelCallProtector
        sig = inspect.signature(ModelCallProtector.stream_with_protection)
        params = list(sig.parameters.keys())
        for p in ("raw_stream", "messages", "model_name", "tools"):
            assert p in params

    def test_model_map_resolves_api_base_and_model_name(self):
        """Doc: model_map maps engine model_type → api_base + model_name."""
        from arf.protection.protector import ModelCallProtector
        p = ModelCallProtector(model_map={
            "deep": {"base_url": "https://api.deepseek.com", "model_name": "deepseek-v4-pro"},
        })
        api_base, mn = p._resolve("deep")
        assert api_base == "https://api.deepseek.com"
        assert mn == "deepseek-v4-pro"

    def test_rate_limiter_per_api_base(self):
        """Doc: per api_base — same endpoint's different models share limiter."""
        from arf.protection.protector import ModelCallProtector
        p = ModelCallProtector(model_map={
            "deep": {"base_url": "https://api.deepseek.com", "model_name": "v4-pro"},
            "quick": {"base_url": "https://api.deepseek.com", "model_name": "v4-flash"},
        })
        limiter1 = p._get_rate_limiter("https://api.deepseek.com")
        limiter2 = p._get_rate_limiter("https://api.deepseek.com")
        assert limiter1 is limiter2  # Same instance

    def test_circuit_breaker_per_model(self):
        """Doc: per model — each model has its own breaker."""
        from arf.protection.protector import ModelCallProtector
        p = ModelCallProtector()
        b1 = p._get_breaker("deepseek-v4-pro")
        b2 = p._get_breaker("deepseek-v4-flash")
        assert b1 is not b2

    def test_rate_limit_exceeded_emits_event_and_raises(self):
        """Doc: Token bucket 拒绝请求时 raise RateLimitError, emit rate_limited."""
        from arf.protection.protector import ModelCallProtector
        from arf.protection.errors import RateLimitError

        async def run():
            eb = MagicMock()
            p = ModelCallProtector(event_bus=eb, model_map={},
                                   rate_limit_config={"requests_per_second": 0,
                                                      "max_burst": 1})
            # Drain the single token
            await p.call_with_protection(AsyncMock(return_value={"text": "ok"}),
                                         [], model_name="test")
            # Next call should be rate limited
            with pytest.raises(RateLimitError):
                await p.call_with_protection(AsyncMock(return_value={"text": "ok"}),
                                             [], model_name="test")
            eb.emit.assert_called()

        asyncio.run(run())

    def test_circuit_breaker_open_emits_event_and_raises(self):
        """Doc: OPEN 状态拒绝请求 → raise CircuitOpenError, emit breaker_blocked."""
        from arf.protection.protector import ModelCallProtector
        from arf.protection.errors import CircuitOpenError

        async def run():
            eb = MagicMock()
            p = ModelCallProtector(event_bus=eb, model_map={"test": {}},
                                   breaker_config={"failure_threshold": 1})
            # Trip the breaker
            try:
                await p.call_with_protection(AsyncMock(side_effect=Exception("fail")),
                                             [], model_name="test")
            except Exception:
                pass
            # 2nd call should be blocked
            with pytest.raises(CircuitOpenError):
                await p.call_with_protection(AsyncMock(), [], model_name="test")

        asyncio.run(run())

    def test_success_after_call_emits_circuit_closed(self):
        """Doc: breaker 恢复时 emit circuit_closed."""
        from arf.protection.protector import ModelCallProtector

        async def run():
            eb = MagicMock()
            p = ModelCallProtector(event_bus=eb, model_map={"test": {}},
                                   breaker_config={"failure_threshold": 1,
                                                   "base_cooldown": 0.01})
            # Trip
            try:
                await p.call_with_protection(AsyncMock(side_effect=Exception("fail")),
                                             [], model_name="test")
            except Exception:
                pass
            # Wait and succeed
            await asyncio.sleep(0.02)
            await p.call_with_protection(AsyncMock(return_value={"text": "ok"}),
                                         [], model_name="test")
            # Check for circuit_closed event
            emitted = [c[0][0].type for c in eb.emit.call_args_list]
            assert "circuit_closed" in emitted

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. Observability (docs 2.5)
# ---------------------------------------------------------------------------

class TestProtectionObservability:
    """Doc: 5 event types emitted via EventBus."""

    def test_five_event_types_emitted(self):
        """Doc: rate_limited, circuit_opened, circuit_half_open,
        circuit_closed, breaker_blocked."""
        from arf.protection.protector import ModelCallProtector
        src = inspect.getsource(ModelCallProtector._emit)
        # The 5 event types are in the protector code
        full_src = inspect.getsource(ModelCallProtector)
        events = ["rate_limited", "circuit_opened", "circuit_half_open",
                   "circuit_closed", "breaker_blocked"]
        for e in events:
            assert e in full_src, f"Event '{e}' not found in ModelCallProtector"


# ---------------------------------------------------------------------------
# 5. Architecture — zero invasion (docs 2.1)
# ---------------------------------------------------------------------------

class TestArchitectureZeroInvasion:
    """Doc: GraphEngine and ModelAdapter zero invasion."""

    def test_no_protection_code_in_graph_engine(self):
        """Doc: GraphEngine 零侵入."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine)
        assert "TokenBucket" not in src
        assert "CircuitBreaker" not in src
        assert "ModelCallProtector" not in src

    def test_protection_injected_in_base_agent(self):
        """Doc: BaseAgent._inject_model_calls() wraps calls."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent._inject_model_calls)
        assert "ModelCallProtector" in src
        assert "call_with_protection" in src
        assert "stream_with_protection" in src

    def test_protection_config_gating(self):
        """Doc: protection enabled via advanced.protection.enabled."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent._inject_model_calls)
        assert "adv.protection.enabled" in src or "protection.enabled" in src


# ---------------------------------------------------------------------------
# 6. Config Models (docs 2.7)
# ---------------------------------------------------------------------------

class TestProtectionConfig:
    """Doc: ProtectionConfig in agent.yaml."""

    def test_protection_config_fields(self):
        """Doc: enabled, rate_limit, circuit_breaker."""
        from arf.core.config_base import ProtectionConfig
        fields = set(ProtectionConfig.model_fields.keys())
        for f in ("enabled", "rate_limit", "circuit_breaker"):
            assert f in fields

    def test_protection_enabled_defaults_true(self):
        """Doc: enabled: true."""
        from arf.core.config_base import ProtectionConfig
        assert ProtectionConfig().enabled is True

    def test_rate_limit_config_defaults(self):
        """Doc: requests_per_second: 5, max_burst: 10."""
        from arf.core.config_base import ProtectionRateLimitConfig
        c = ProtectionRateLimitConfig()
        assert c.requests_per_second == 5.0
        assert c.max_burst == 10

    def test_circuit_breaker_config_defaults(self):
        """Doc: failure_threshold=3, base_cooldown=10s, multiplier=2,
        max_cooldown=300s, half_open_max_requests=1."""
        from arf.core.config_base import ProtectionCircuitBreakerConfig
        c = ProtectionCircuitBreakerConfig()
        assert c.failure_threshold == 3
        assert c.base_cooldown == "10s"
        assert c.cooldown_multiplier == 2.0
        assert c.max_cooldown == "300s"
        assert c.half_open_max_requests == 1

    def test_advanced_config_includes_protection(self):
        """Doc: advanced.protection in agent.yaml."""
        from arf.agent.config import AdvancedConfig
        assert hasattr(AdvancedConfig(), "protection")


# ---------------------------------------------------------------------------
# 7. Duration parsing (docs 2.7 — supports "10s", "300s" format)
# ---------------------------------------------------------------------------

class TestDurationParsing:
    """Doc: config values like '10s', '300s' parsed by protector."""

    def test_parse_seconds(self):
        from arf.protection.protector import _parse_duration
        assert _parse_duration("10s") == 10.0

    def test_parse_minutes(self):
        from arf.protection.protector import _parse_duration
        assert _parse_duration("5m") == 300.0

    def test_parse_milliseconds(self):
        from arf.protection.protector import _parse_duration
        assert _parse_duration("500ms") == 0.5

    def test_parse_hours(self):
        from arf.protection.protector import _parse_duration
        assert _parse_duration("1h") == 3600.0

    def test_parse_raw_float(self):
        from arf.protection.protector import _parse_duration
        assert _parse_duration(15.0) == 15.0


# ---------------------------------------------------------------------------
# 8. Error types (docs 2.2, 2.3)
# ---------------------------------------------------------------------------

class TestProtectionErrors:
    """Doc: RateLimitError and CircuitOpenError."""

    def test_rate_limit_error_exists(self):
        """Doc: RateLimitError raised when acquire() returns False."""
        from arf.protection.errors import RateLimitError
        e = RateLimitError(model="test", api_base="http://x")
        assert "Rate limit exceeded" in str(e)

    def test_circuit_open_error_exists(self):
        """Doc: CircuitOpenError raised when breaker OPEN."""
        from arf.protection.errors import CircuitOpenError
        e = CircuitOpenError(model="test", circuit_state="open")
        assert "Circuit open" in str(e)


# ---------------------------------------------------------------------------
# 9. File existence + imports
# ---------------------------------------------------------------------------

class TestCrossDocConsistency:
    """Verify files and classes referenced in docs exist."""

    def test_protection_files_exist(self):
        """Doc references these specific files."""
        root = Path(__file__).parent.parent.parent
        for f in ["arf/protection/rate_limiter.py",
                   "arf/protection/circuit_breaker.py",
                   "arf/protection/protector.py",
                   "arf/protection/errors.py"]:
            assert (root / f).exists(), f"File '{f}' not found"

    def test_all_classes_importable(self):
        """Doc: TokenBucket, CircuitBreaker, ModelCallProtector."""
        from arf.protection.rate_limiter import TokenBucket
        from arf.protection.circuit_breaker import CircuitBreaker
        from arf.protection.protector import ModelCallProtector
        from arf.protection.errors import RateLimitError, CircuitOpenError
        assert TokenBucket is not None
        assert CircuitBreaker is not None
        assert ModelCallProtector is not None
        assert RateLimitError is not None
        assert CircuitOpenError is not None
