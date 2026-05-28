# ARF Fact-Check Report — 2026-05-28 — API Protection

**Domain**: API Protection (`docs/api-protection.md` vs `arf/protection/`)
**Methodology**: TDD-style — 40 tests. 39 pass, 1 xfail.

---

## Summary

| Metric | Count |
|--------|-------|
| Total doc claims tested | 40 |
| Passed | 39 |
| Xfail (known finding) | 1 |

## Findings

### Info (FIXED)

**F1. `model_retry=3` dead parameter**

Doc 2.6: engine-level retry removed from `DefaultErrorPolicy`. Behavior was correct — `on_model_error()` never retried. But `__init__`, `ErrorConfig`, and `base.py` still carried `model_retry=3` as dead code.

**Fix**: Removed `model_retry` from `DefaultErrorPolicy.__init__`, `ErrorConfig`, `base.py`, and all test callers. Engine-level model retry is now completely gone. (2026-05-28)

## Verified Claims (39 passing)

TokenBucket, CircuitBreaker state machine, ModelCallProtector composition, 5 observability events, config models, retry simplification behavior, zero-invasion architecture — all consistent.

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
├── TestCrossDocConsistency (2 tests)
└── TestDeadModelRetryParam (1 xfail)
```
