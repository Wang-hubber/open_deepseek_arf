# ARF Fact-Check Report — 2026-05-28 — API Protection

**Domain**: API Protection (`docs/api-protection.md` vs `arf/protection/`)
**Methodology**: TDD-style — 39 tests derived from doc claims. All passed.

---

## Summary

| Metric | Count |
|--------|-------|
| Total doc claims tested | 39 |
| Passed | 39 |
| Failed | 0 |
| Findings | 0 |

**Overall assessment**: API Protection is the cleanest domain yet. Zero discrepancies. Docs and code are perfectly aligned — TokenBucket behavior, CircuitBreaker state machine, ModelCallProtector composite, observability events, config models, DefaultErrorPolicy retry removal, all check out.

---

## Verified Claims

### TokenBucket — all consistent
- `capacity` (max burst), `rate` (tokens/sec refill), `tokens` (current count)
- `acquire()` returns True/False, never blocks
- `asyncio.Lock` thread safety
- Tokens capped at capacity, refill by elapsed time

### CircuitBreaker — all consistent
- Three states: CLOSED, OPEN, HALF_OPEN
- CLOSED → OPEN after `failure_threshold` consecutive failures (default 3)
- OPEN → HALF_OPEN after `open_duration` (default 10s, exponential backoff)
- HALF_OPEN → CLOSED on first success (resets cooldown)
- HALF_OPEN → OPEN on first failure (cooldown *= 2, capped at 300s)
- `half_open_max_requests=1` (one probe at a time)

### ModelCallProtector — all consistent
- `call_with_protection(raw_call, messages, model_name, tools)`
- `stream_with_protection(raw_stream, messages, model_name, tools)`
- `model_map` resolves engine model_type → api_base + model_name
- Per api_base rate limiting (same endpoint models share limiter)
- Per model circuit breaking (each model has own breaker)
- `RateLimitError` on bucket empty, `CircuitOpenError` on breaker OPEN
- Events emitted via EventBus on all state transitions

### Observability — all consistent
- 5 event types: `rate_limited`, `circuit_opened`, `circuit_half_open`, `circuit_closed`, `breaker_blocked`

### Architecture — all consistent
- GraphEngine zero invasion (no protection imports)
- ModelAdapter zero invasion (protection wraps from outside)
- Injected at `BaseAgent._inject_model_calls()` via decorator pattern
- Gated by `advanced.protection.enabled`

### Retry Simplification — all consistent
- `DefaultErrorPolicy.on_model_error()` does NOT retry (line comment confirms)
- `ModelAdapter._call_with_retry()` keeps 3 retries with 1.5x backoff for 429/network
- `_resolve_fallback` chains `error_policy.on_model_error()` → `model_router.fallback_from()`

### Config Models — all consistent
- `ProtectionConfig`: `enabled=True`, `rate_limit`, `circuit_breaker`
- `ProtectionRateLimitConfig`: `requests_per_second=5.0`, `max_burst=10`
- `ProtectionCircuitBreakerConfig`: `failure_threshold=3`, `base_cooldown="10s"`, `cooldown_multiplier=2.0`, `max_cooldown="300s"`, `half_open_max_requests=1`
- `_parse_duration()` supports: ms, s, m, h, raw float

### Error Types — all consistent
- `RateLimitError(model, api_base)`
- `CircuitOpenError(model, circuit_state)`

### File Existence — all consistent
- `arf/protection/rate_limiter.py`, `circuit_breaker.py`, `protector.py`, `errors.py`

---

## Test Suite

```
tests/fact_check/test_protection_domain.py
├── TestTokenBucket (6 tests)
├── TestCircuitBreakerStates (7 tests)
├── TestModelCallProtector (8 tests)
├── TestProtectionObservability (1 test)
├── TestArchitectureZeroInvasion (3 tests)
├── TestProtectionConfig (5 tests)
├── TestDurationParsing (5 tests)
├── TestProtectionErrors (2 tests)
└── TestCrossDocConsistency (2 tests)
```

Run: `pytest tests/fact_check/test_protection_domain.py -v`
