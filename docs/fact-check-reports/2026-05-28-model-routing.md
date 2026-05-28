# ARF Fact-Check Report — 2026-05-28 — Model Routing

**Domain**: Model Routing (`docs/model-routing.md` vs `arf/routing/` + `arf/core/model_adapter.py`)
**Methodology**: TDD-style — 49 tests derived from doc claims. 47 pass, 2 xfail (documented findings).

---

## Summary

| Metric | Count |
|--------|-------|
| Total doc claims tested | 49 |
| Passed | 47 |
| Xfail (known findings) | 2 |
| Deep manual findings | 0 (all caught by TDD) |

**Overall assessment**: Routing domain docs are well-maintained. Protocol, TwoTierRouter behavior, LLM classifier, engine integration order, config models, ModelAdapter retry, and system model resolution all check out. **2 issues found, both fixed.**

---

## Findings

### Critical (FIXED)

**F1. "static" strategy not implemented** (`xfail: test_static_strategy_checked_in_base_agent`)

Doc 2.12 describes two strategies:
> `two_tier` — LLM 分类器动态判断，每次 turn 可切换模型
> `static` — 始终使用 `default`，不分类

`RoutingConfig.strategy` has `Literal["two_tier", "static"]` with default `"two_tier"`. But `BaseAgent.__init__` (line 346) only checks `if adv and adv.routing and len(config.models) > 1` — **never checks `adv.routing.strategy`**. A TwoTierRouter with LLM classifier is always created regardless of strategy value.

Effect: setting `strategy: static` in agent.yaml has **zero effect** — the system still classifies every turn.

**Fix**: Added `if adv.routing.strategy == "static":` branch in `BaseAgent.__init__` that creates TwoTierRouter without `classifier_call`, so `classify()` always returns `"medium"` → maps to `default` model. (2026-05-28)

### Warning (FIXED)

**F2. Empty `default` fallback chain doesn't match doc** (`xfail: test_engine_guards_empty_route_result`)

Doc 2.8 claims:
> default 为空 → 回退到 `state["current_model"]`（引擎初始 model）

But the engine code (`graph.py:577-580`):
```python
model = state["current_model"]
if self.model_router:
    model = await self.model_router.route(...)
    state["current_model"] = model
```

`route()` calls `self._cfg.classify.get(level, self._cfg.default)`. If `default` is `""` (empty, which is the actual default value), and the classification level isn't in the classify dict, `route()` returns `""`. The engine then overwrites `state["current_model"]` with `""` **without checking for empty result**.

Effect: if `RoutingConfig.default` is empty (the default), unrecognized classification levels set `current_model` to `""`, causing downstream failures.

**Fix**: Changed `model = await self.model_router.route(...)` to `routed = await self.model_router.route(...); model = routed or model` in both `invoke()` and `astream()` — empty route() result now falls back to `state["current_model"]` as documented. (2026-05-28)

---

## Verified Claims (all 47 passing tests)

### Protocol — all consistent
- `ModelRouter` has 3 methods: `route(query, history)`, `classify(query)`, `fallback_from(model_name)`
- All properly exported from `arf.core.protocols`

### TwoTierRouter — all consistent
- `route()` calls `classify()` then maps via `config.classify` dict
- `classify()` returns `"medium"` when no classifier configured
- `fallback_from()` reads from `config.fallback` dict, returns `None` for unknown
- Missing classification mapping → `config.default`
- Constructor: `(config, models, classifier_call)`
- Exported from `arf.routing`

### LLM Classifier — all consistent
- Defined as closure in `BaseAgent.__init__`
- Prompt: "Classify this task as 'medium' or 'complex'"
- Input truncated to 300 chars (`query[:300]`)
- Invalid/exception → returns `"medium"`
- Uses system model

### Engine Integration — all consistent
- Routing executes before compaction (verified via source order)
- Window size from routed model passed to compaction
- Route called per-turn inside the while loop
- `state["current_model"]` updated after routing

### Auto-Derive — all consistent
- `auto_derive(models_count >= 2)` → `RoutingConfig(strategy="two_tier")`
- Single model → no auto routing

### Fallback Chain — all consistent
- `_resolve_fallback()` chains `error_policy.on_model_error()` → `model_router.fallback_from()`
- Returns `None` when no router configured

### KV Cache — all consistent
- No KV cache management code in `arf/` (verified via grep)

### ModelAdapter — all consistent
- Retry on 429, 500, 502, 503, 504
- Default max retries: 3
- Backoff base: 1.5x
- Empty API key → `"sk-placeholder"` (verified with patched OpenAI)
- `thinking_enabled` in `_PROVIDER_KEYS`
- `chat_stream_full()` exists
- `_call_with_retry()` and `_should_retry()` exist

### Config Models — all consistent
- `RoutingConfig` fields: `strategy`, `default`, `classify`, `fallback`, `background`
- Defaults: `strategy="two_tier"`, `default=""`, `classify={}`, `fallback={}`
- `AdvancedConfig` includes `routing` field

### System Model — all consistent
- Created in `BaseAgent.__init__`
- `temperature=0.3`, `thinking_enabled="false"`, `max_tokens=1024`
- Falls back to `config.models[0]` when system_model name not found
- Shares API key with user models (`api_key_env`)
- At least 4 consumers (memory write, retrieve, compaction, classify)
- Uses `if _system_model_call:` pattern for conditional degradation

### File Existence — all consistent
- `arf/routing/two_tier.py`, `arf/core/protocols/routing.py`, `arf/core/model_adapter.py` all exist

---

## Test Suite

```
tests/fact_check/test_routing_domain.py
├── TestModelRouterProtocol (5 tests)
├── TestTwoTierRouter (6 tests)
├── TestLLMClassifier (5 tests)
├── TestEngineIntegration (4 tests)
├── TestAutoDerive (2 tests)
├── TestDegradationChain (3 tests)
├── TestKVCache (1 test)
├── TestModelAdapter (7 tests)
├── TestRoutingConfig (4 tests)
├── TestSystemModel (8 tests)
├── TestCrossDocConsistency (2 tests)
├── TestStaticStrategyImplemented (1 xfail)
└── TestDefaultModelFallback (1 xfail)
```

Run: `pytest tests/fact_check/test_routing_domain.py -v`
