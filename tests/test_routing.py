"""Tests for TwoTierRouter — behavior-driven unit tests.

Covers: route() classification→mapping, classify() fallback,
fallback_from() lookup, constructor, edge cases, and degradation chain.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from arf.routing.two_tier import TwoTierRouter
from arf.core.config_base import RoutingConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_cfg():
    return RoutingConfig(
        strategy="two_tier",
        default="quick",
        classify={"medium": "quick", "complex": "deep"},
        fallback={"deep": "quick"},
    )


@pytest.fixture
def router_no_classifier(default_cfg):
    """Router without a classifier callable (static or fallback mode)."""
    return TwoTierRouter(default_cfg, models=["quick", "deep"])


@pytest.fixture
def router_with_classifier(default_cfg):
    """Router with a mock classifier."""
    classifier = AsyncMock(return_value="medium")
    return TwoTierRouter(default_cfg, models=["quick", "deep"], classifier_call=classifier)


# ---------------------------------------------------------------------------
# route()
# ---------------------------------------------------------------------------

class TestRoute:
    """Doc 2.4: route(query, history) → classifies then maps to model name."""

    def test_classifies_as_complex_maps_to_deep(self, default_cfg):
        """Doc: classify returns 'complex' → maps to 'deep'."""
        classifier = AsyncMock(return_value="complex")
        router = TwoTierRouter(default_cfg, models=["quick", "deep"], classifier_call=classifier)

        async def run():
            return await router.route("write a raytracer in Rust", [])

        result = asyncio.run(run())
        assert result == "deep"

    def test_classifies_as_medium_maps_to_quick(self, default_cfg):
        """Doc: classify returns 'medium' → maps to 'quick'."""
        classifier = AsyncMock(return_value="medium")
        router = TwoTierRouter(default_cfg, models=["quick", "deep"], classifier_call=classifier)

        async def run():
            return await router.route("what is 2+2?", [])

        result = asyncio.run(run())
        assert result == "quick"

    def test_passes_query_to_classifier(self, router_with_classifier):
        """Doc: classify(query) is called with the user query."""
        async def run():
            await router_with_classifier.route("explain monads", [])
            router_with_classifier._classify.assert_awaited_once_with("explain monads")

        asyncio.run(run())

    def test_unmapped_classification_uses_default(self, default_cfg):
        """Doc: classification result not in classify dict → config.default."""
        cfg = RoutingConfig(default="quick", classify={"medium": "quick"})
        classifier = AsyncMock(return_value="some_unknown_level")
        router = TwoTierRouter(cfg, models=["quick"], classifier_call=classifier)

        async def run():
            return await router.route("some query", [])

        result = asyncio.run(run())
        assert result == "quick"

    def test_empty_string_default_still_returned(self):
        """When default is '' and classify returns unknown, route returns ''.
        The engine layer guards with `routed or model` for final safety net."""
        cfg = RoutingConfig(default="")
        classifier = AsyncMock(return_value="unknown")
        router = TwoTierRouter(cfg, models=["quick"], classifier_call=classifier)

        async def run():
            return await router.route("query", [])

        result = asyncio.run(run())
        assert result == ""

    def test_no_classifier_routes_via_default(self, router_no_classifier):
        """Doc: 无分类器 → classify returns 'medium' → maps via config.classify."""
        async def run():
            return await router_no_classifier.route("any query", [])

        result = asyncio.run(run())
        assert result in ("quick", "medium")  # maps via classify dict or default

    def test_accepts_empty_history(self, default_cfg):
        """Edge case: empty history list should not break route()."""
        classifier = AsyncMock(return_value="medium")
        router = TwoTierRouter(default_cfg, models=["quick"], classifier_call=classifier)

        async def run():
            return await router.route("hi", [])

        result = asyncio.run(run())
        assert result == "quick"


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------

class TestClassify:
    """Doc 2.4: classify(query) — with and without classifier."""

    def test_returns_classifier_result_when_set(self):
        """Doc: if self._classify → return await self._classify(query)."""
        classifier = AsyncMock(return_value="complex")
        router = TwoTierRouter(
            RoutingConfig(), models=["quick", "deep"], classifier_call=classifier,
        )

        async def run():
            return await router.classify("build a web app")

        result = asyncio.run(run())
        assert result == "complex"

    def test_returns_medium_when_no_classifier(self):
        """Doc: 无分类器时返回 'medium'（安全默认）."""
        router = TwoTierRouter(RoutingConfig(), models=["quick"])

        async def run():
            return await router.classify("any query")

        result = asyncio.run(run())
        assert result == "medium"

    def test_returns_medium_even_for_complex_sounding_queries(self, router_no_classifier):
        """Without a classifier, everything is 'medium' — conservative default."""
        async def run():
            return await router_no_classifier.classify(
                "design a distributed database with consensus protocol"
            )

        result = asyncio.run(run())
        assert result == "medium"


# ---------------------------------------------------------------------------
# fallback_from()
# ---------------------------------------------------------------------------

class TestFallbackFrom:
    """Doc 2.4: fallback_from returns config.fallback mapping."""

    def test_returns_fallback_model_when_configured(self, router_no_classifier):
        """Doc: returns the fallback model for a given model name."""
        assert router_no_classifier.fallback_from("deep") == "quick"

    def test_returns_none_for_unknown_model(self, router_no_classifier):
        """Model not in fallback dict → returns None."""
        assert router_no_classifier.fallback_from("quick") is None
        assert router_no_classifier.fallback_from("nonexistent") is None

    def test_returns_none_with_empty_fallback_config(self):
        """When fallback dict is empty, all lookups return None."""
        cfg = RoutingConfig(default="quick")
        router = TwoTierRouter(cfg, models=["quick", "deep"])
        assert router.fallback_from("deep") is None
        assert router.fallback_from("quick") is None


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    """Doc 2.4: TwoTierRouter(config, models, classifier_call)."""

    def test_stores_config(self, router_no_classifier):
        assert router_no_classifier._cfg is not None
        assert router_no_classifier._cfg.default == "quick"

    def test_stores_models_list(self, router_no_classifier):
        assert router_no_classifier._models == ["quick", "deep"]

    def test_stores_classifier_call(self, router_with_classifier):
        assert router_with_classifier._classify is not None

    def test_classifier_call_is_none_when_not_provided(self, router_no_classifier):
        assert router_no_classifier._classify is None

    def test_accepts_empty_models_list(self):
        """Edge case: empty models list should not crash constructor."""
        router = TwoTierRouter(RoutingConfig(), models=[])
        assert router._models == []


# ---------------------------------------------------------------------------
# Degradation Chain (Doc 2.8)
# ---------------------------------------------------------------------------

class TestDegradationChain:
    """Doc 2.8: four-level fallback chain — Router-level behavior."""

    def test_level_1_classify_exception_propagates_to_caller(self):
        """Router does NOT catch classifier exceptions — caller (BaseAgent) does.
        The classifier closure itself has try/except, so Router never sees exceptions."""
        failing_classifier = AsyncMock(side_effect=RuntimeError("API down"))
        router = TwoTierRouter(
            RoutingConfig(), models=["quick"], classifier_call=failing_classifier,
        )

        async def run():
            with pytest.raises(RuntimeError, match="API down"):
                await router.classify("query")

        asyncio.run(run())

    def test_level_2_unmapped_level_uses_default(self):
        """Doc: 分类结果不在 classify dict 中 → 使用 config.default."""
        cfg = RoutingConfig(default="quick", classify={})
        classifier = AsyncMock(return_value="complex")
        router = TwoTierRouter(cfg, models=["quick", "deep"], classifier_call=classifier)

        async def run():
            return await router.route("query", [])

        result = asyncio.run(run())
        assert result == "quick"

    def test_level_3_empty_default_returns_empty_string(self):
        """Doc: default 为空 → Router returns ''; Engine guards with `model or routed`."""
        cfg = RoutingConfig(default="", classify={})
        classifier = AsyncMock(return_value="complex")
        router = TwoTierRouter(cfg, models=["quick", "deep"], classifier_call=classifier)

        async def run():
            return await router.route("query", [])

        result = asyncio.run(run())
        assert result == ""


# ---------------------------------------------------------------------------
# Static Strategy (Doc 2.12)
# ---------------------------------------------------------------------------

class TestStaticStrategy:
    """Doc 2.12: static strategy = always use default, no classification."""

    def test_no_classifier_means_always_medium_then_default(self):
        """Static strategy: no classifier → classify()→'medium' → maps or defaults."""
        cfg = RoutingConfig(strategy="static", default="quick")
        router = TwoTierRouter(cfg, models=["quick", "deep"])  # no classifier_call

        async def run():
            return await router.route("complex multi-step task", [])

        result = asyncio.run(run())
        # No classifier → classify returns "medium" → maps via classify dict
        # If no classify mapping, returns default
        assert result in ("medium", "quick")

    def test_static_strategy_ignores_query_complexity(self):
        """Static strategy should return same model regardless of query."""
        cfg = RoutingConfig(strategy="static", default="quick")
        router = TwoTierRouter(cfg, models=["quick", "deep"])

        async def run():
            simple = await router.route("hello", [])
            complex_q = await router.route("build a distributed system", [])
            return simple, complex_q

        simple, complex_q = asyncio.run(run())
        assert simple == complex_q
