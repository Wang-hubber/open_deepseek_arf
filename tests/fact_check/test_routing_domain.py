"""Fact-check tests: Routing Domain — docs/model-routing.md vs arf/routing/ + arf/core/model_adapter.py.

Each test validates a specific claim made in the documentation against actual code.
PASS = doc/code consistent. FAIL = discrepancy found (fact-check finding).
"""

import inspect
import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 1. ModelRouter Protocol (docs 2.3)
# ---------------------------------------------------------------------------

class TestModelRouterProtocol:
    """Doc: ModelRouter protocol in arf/core/protocols/routing.py."""

    def test_protocol_has_three_methods(self):
        """Doc: route(query, history), classify(query), fallback_from(model_name)."""
        from arf.core.protocols.routing import ModelRouter
        methods = {n for n in dir(ModelRouter) if not n.startswith("_")}
        assert "route" in methods
        assert "classify" in methods
        assert "fallback_from" in methods

    def test_route_signature(self):
        """Doc: async def route(self, query: str, history: list[dict]) -> str."""
        from arf.core.protocols.routing import ModelRouter
        sig = inspect.signature(ModelRouter.route)
        params = list(sig.parameters.keys())
        assert "query" in params
        assert "history" in params

    def test_classify_signature(self):
        """Doc: async def classify(self, query: str) -> str."""
        from arf.core.protocols.routing import ModelRouter
        sig = inspect.signature(ModelRouter.classify)
        assert "query" in sig.parameters

    def test_fallback_from_signature(self):
        """Doc: def fallback_from(self, model_name: str) -> str | None."""
        from arf.core.protocols.routing import ModelRouter
        sig = inspect.signature(ModelRouter.fallback_from)
        assert "model_name" in sig.parameters

    def test_protocol_exported(self):
        """Doc: ModelRouter should be importable from protocols."""
        from arf.core.protocols import ModelRouter
        assert ModelRouter is not None


# ---------------------------------------------------------------------------
# 2. TwoTierRouter Implementation (docs 2.4)
# ---------------------------------------------------------------------------

class TestTwoTierRouter:
    """Doc: TwoTierRouter in arf/routing/two_tier.py."""

    def test_route_calls_classify_then_maps_to_model(self):
        """Doc: route() calls classify() then maps via config.classify dict."""
        from arf.routing.two_tier import TwoTierRouter
        from arf.core.config_base import RoutingConfig

        cfg = RoutingConfig(default="quick", classify={"medium": "quick", "complex": "deep"})

        async def _classify(query):
            return "complex"

        router = TwoTierRouter(cfg, models=["quick", "deep"], classifier_call=_classify)

        async def run():
            result = await router.route("write a compiler", [])
            assert result == "deep"

        asyncio.run(run())

    def test_classify_returns_medium_when_no_classifier(self):
        """Doc: 无分类器时返回 'medium'（安全默认）."""
        from arf.routing.two_tier import TwoTierRouter
        from arf.core.config_base import RoutingConfig

        cfg = RoutingConfig(default="quick")
        router = TwoTierRouter(cfg, models=["quick"])

        async def run():
            result = await router.classify("any query")
            assert result == "medium"

        asyncio.run(run())

    def test_fallback_from_returns_from_config(self):
        """Doc: fallback_from() returns from config.fallback dict."""
        from arf.routing.two_tier import TwoTierRouter
        from arf.core.config_base import RoutingConfig

        cfg = RoutingConfig(fallback={"deep": "quick"})
        router = TwoTierRouter(cfg, models=["quick", "deep"])
        assert router.fallback_from("deep") == "quick"
        assert router.fallback_from("quick") is None

    def test_classify_mapping_missing_uses_default(self):
        """Doc: 分类结果不在 classify dict 中时使用 config.default."""
        from arf.routing.two_tier import TwoTierRouter
        from arf.core.config_base import RoutingConfig

        cfg = RoutingConfig(default="quick", classify={"medium": "quick"})

        async def _classify(query):
            return "unknown_level"

        router = TwoTierRouter(cfg, models=["quick"], classifier_call=_classify)

        async def run():
            result = await router.route("query", [])
            assert result == "quick"

        asyncio.run(run())

    def test_constructor_accepts_config_models_classifier(self):
        """Doc: TwoTierRouter(config, models, classifier_call)."""
        from arf.routing.two_tier import TwoTierRouter
        sig = inspect.signature(TwoTierRouter.__init__)
        params = list(sig.parameters.keys())
        assert "config" in params
        assert "models" in params
        assert "classifier_call" in params

    def test_exported_from_module_init(self):
        """Doc: TwoTierRouter exported from arf.routing."""
        from arf.routing import TwoTierRouter
        assert TwoTierRouter is not None


# ---------------------------------------------------------------------------
# 3. LLM Classifier (docs 2.5)
# ---------------------------------------------------------------------------

class TestLLMClassifier:
    """Doc: classifier closure in base.py, uses system model."""

    def test_classifier_exists_in_base_agent(self):
        """Doc: classifier defined as closure in base.py."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "_classify" in src, "Classifier closure not found in BaseAgent.__init__"

    def test_classifier_truncates_query_to_300_chars(self):
        """Doc: 输入截断至 300 字符."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "[:300]" in src, "Query truncation to 300 chars not found"

    def test_classifier_returns_medium_on_exception(self):
        """Doc: 分类失败或异常一律返回 'medium'."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert 'return "medium"' in src, "Exception fallback to 'medium' not found"
        assert "except Exception:" in src or "except" in src

    def test_classifier_validates_result_in_medium_or_complex(self):
        """Doc: result if in ('medium', 'complex') else 'medium'."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "medium" in src and "complex" in src

    def test_classifier_prompt_asks_for_medium_or_complex(self):
        """Doc: prompt classifies as 'medium' or 'complex'."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "Classify this task as 'medium' or 'complex'" in src


# ---------------------------------------------------------------------------
# 4. Engine Integration (docs 2.6)
# ---------------------------------------------------------------------------

class TestEngineIntegration:
    """Doc: routing integration in GraphEngine."""

    def test_routing_before_compaction(self):
        """Doc: 路由在压缩之前执行（graph.py），确保压缩使用正确模型的窗口大小."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        route_pos = src.find("model_router.route")
        compact_pos = src.find("compaction.should_compact")
        assert route_pos > 0, "route() call not found in invoke"
        assert compact_pos > 0, "should_compact() not found in invoke"
        assert route_pos < compact_pos, (
            f"Routing (pos {route_pos}) must come before compaction (pos {compact_pos})"
        )

    def test_window_size_passed_from_routed_model(self):
        """Doc: 压缩使用正确模型的窗口大小."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        # window should come from _model_windows dict using the routed model
        assert "window_size=window" in src or "window_size=windows" in src or "_model_windows" in src

    def test_routing_per_turn(self):
        """Doc: 每次 turn 之间可无缝切换模型."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        # route() should be inside the while loop, not outside
        assert "model_router.route" in src

    def test_state_current_model_updated_after_routing(self):
        """Doc: state['current_model'] updated with routed model."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert 'state["current_model"] = model' in src or "state['current_model'] = model" in src


# ---------------------------------------------------------------------------
# 5. Auto-Derive (docs 2.7)
# ---------------------------------------------------------------------------

class TestAutoDerive:
    """Doc: auto_derive enables two_tier when multiple models and no explicit routing."""

    def test_auto_derive_enables_two_tier_with_multi_models(self):
        """Doc: models > 1 且未显式指定 routing → auto two_tier."""
        from arf.agent.config import AdvancedConfig
        adv = AdvancedConfig.auto_derive(tools_count=5, models_count=3)
        assert adv.routing is not None
        assert adv.routing.strategy == "two_tier"

    def test_auto_derive_no_routing_with_single_model(self):
        """Doc: models <= 1 → no auto routing."""
        from arf.agent.config import AdvancedConfig
        adv = AdvancedConfig.auto_derive(tools_count=5, models_count=1)
        assert adv.routing is None


# ---------------------------------------------------------------------------
# 6. Fallback / Degradation Chain (docs 2.8)
# ---------------------------------------------------------------------------

class TestDegradationChain:
    """Doc: four-level fallback chain."""

    def test_resolve_fallback_method_exists(self):
        """Doc: _resolve_fallback()串联 error_policy + model_router."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine._resolve_fallback)
        assert "error_policy" in src
        assert "model_router" in src
        assert "fallback_from" in src

    def test_fallback_returns_none_when_no_router(self):
        """Doc: fallback returns None when no router configured."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine._resolve_fallback)
        assert "return None" in src

    def test_classify_exception_returns_medium(self):
        """Doc: LLM 分类器异常 → 返回 'medium' → quick."""
        # Tested via classifier source inspection in TestLLMClassifier
        pass  # Verified by test_classifier_returns_medium_on_exception


class TestStaticStrategyImplemented:
    """Doc 2.12: 'static' strategy = always use default, no classification."""

    def test_static_strategy_checked_in_base_agent(self):
        """Doc: strategy='static' → no classification, always use default."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        routing_block_start = src.find("if adv and adv.routing")
        routing_block = src[routing_block_start:routing_block_start + 600]
        assert "strategy" in routing_block, (
            "base.py should check routing.strategy to decide between "
            "'static' (no classifier) and 'two_tier' (with classifier)"
        )


class TestDefaultModelFallback:
    """Doc 2.8: empty default → falls back to state['current_model']."""

    def test_engine_guards_empty_route_result(self):
        """Doc: default 为空 → 回退到 state['current_model'].
        Engine should guard: if route() returns falsy, keep current_model."""
        import inspect as _ins
        from arf.engine.graph import GraphEngine
        src = _ins.getsource(GraphEngine.invoke)
        route_idx = src.find("model_router.route")
        section = src[route_idx:route_idx + 200]
        has_guard = ('or model' in section or
                     'routed or' in section or
                     'model or' in section)
        assert has_guard, (
            "Engine should guard against empty route() result to implement "
            "the doc-described fallback to state['current_model']"
        )


# ---------------------------------------------------------------------------
# 7. KV Cache (docs 2.9)
# ---------------------------------------------------------------------------

class TestKVCache:
    """Doc: 框架有意不介入 KV cache 管理."""

    def test_no_kv_cache_code_in_framework(self):
        """Doc: KV cache 由推理侧管理，框架不操作缓存生命周期."""
        import subprocess
        result = subprocess.run(
            ["grep", "-rli", "kv.cache|kv_cache|kvcache", "arf/"],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent
        )
        assert result.returncode != 0 or result.stdout.strip() == "", (
            f"KV cache management found in: {result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# 8. ModelAdapter (docs 2.10)
# ---------------------------------------------------------------------------

class TestModelAdapter:
    """Doc: ModelAdapter features in arf/core/model_adapter.py."""

    def test_retry_status_codes(self):
        """Doc: 429/5xx 等瞬时错误自动重试."""
        from arf.core.model_adapter import RETRYABLE_STATUS
        assert 429 in RETRYABLE_STATUS
        assert 500 in RETRYABLE_STATUS
        assert 502 in RETRYABLE_STATUS
        assert 503 in RETRYABLE_STATUS
        assert 504 in RETRYABLE_STATUS

    def test_default_max_retries_is_3(self):
        """Doc: 默认 3 次重试."""
        from arf.core.model_adapter import MAX_RETRIES
        assert MAX_RETRIES == 3

    def test_retry_backoff_base(self):
        """Doc: 退避基数 1.5x."""
        from arf.core.model_adapter import RETRY_BACKOFF_BASE
        assert RETRY_BACKOFF_BASE == 1.5

    def test_empty_api_key_uses_placeholder(self):
        """Doc: api_key 为空时使用 'sk-placeholder'."""
        with patch("arf.core.model_adapter.AsyncOpenAI") as mock_cls:
            from arf.core.model_adapter import ModelAdapter
            ModelAdapter({"api_key": "", "base_url": "http://x", "model_name": "x"})
            mock_cls.assert_called_once_with(
                base_url="http://x",
                api_key="sk-placeholder",
            )

    def test_thinking_enabled_in_provider_keys(self):
        """Doc: thinking_enabled 翻译为 DeepSeek thinking 格式."""
        from arf.core.model_adapter import ModelAdapter
        assert "thinking_enabled" in ModelAdapter._PROVIDER_KEYS

    def test_stream_method_exists(self):
        """Doc: chat_stream_full() 产出 chunk、tool_call、usage、error 四种事件."""
        from arf.core.model_adapter import ModelAdapter
        assert hasattr(ModelAdapter, 'chat_stream_full')

    def test_retry_method_exists(self):
        """Doc: _call_with_retry exponential backoff."""
        from arf.core.model_adapter import ModelAdapter
        assert hasattr(ModelAdapter, '_call_with_retry')
        assert hasattr(ModelAdapter, '_should_retry')


# ---------------------------------------------------------------------------
# 9. Config Models (docs 2.11, 2.12)
# ---------------------------------------------------------------------------

class TestRoutingConfig:
    """Doc: RoutingConfig in agent.yaml."""

    def test_routing_config_fields(self):
        """Doc: strategy (two_tier|static), default, classify, fallback, background."""
        from arf.core.config_base import RoutingConfig
        fields = set(RoutingConfig.model_fields.keys())
        for f in ("strategy", "default", "classify", "fallback", "background"):
            assert f in fields, f"Field '{f}' missing from RoutingConfig"

    def test_routing_config_defaults(self):
        """Doc: strategy defaults to 'two_tier'."""
        from arf.core.config_base import RoutingConfig
        c = RoutingConfig()
        assert c.strategy == "two_tier"
        assert c.default == ""
        assert c.classify == {}
        assert c.fallback == {}

    def test_advanced_config_includes_routing(self):
        """Doc: advanced.routing in agent.yaml."""
        from arf.agent.config import AdvancedConfig
        adv = AdvancedConfig()
        assert hasattr(adv, "routing")

    def test_background_field_exists(self):
        """Doc: RoutingConfig has background field (reserved for future)."""
        from arf.core.config_base import RoutingConfig
        c = RoutingConfig()
        assert hasattr(c, "background")


# ---------------------------------------------------------------------------
# 10. System Model (docs 2.13)
# ---------------------------------------------------------------------------

class TestSystemModel:
    """Doc: system model for framework background tasks."""

    def test_system_model_created_in_base_agent(self):
        """Doc: _system_model_call defined in base.py."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "_system_model_call" in src

    def test_system_model_temperature_is_0_3(self):
        """Doc: temperature: 0.3."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "0.3" in src

    def test_system_model_thinking_disabled(self):
        """Doc: thinking_enabled: false."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "thinking_enabled" in src

    def test_system_model_max_tokens_is_1024(self):
        """Doc: max_tokens=1024."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "1024" in src

    def test_system_model_fallback_to_first_model(self):
        """Doc: 找不到 → 回退到 config.models[0]."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "config.models[0]" in src

    def test_system_model_shared_api_key(self):
        """Doc: system model 与用户模型共享同一个 API key（api_key_env）."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "api_key_env" in src

    def test_three_consumers_reference_system_model_call(self):
        """Doc: _system_model_call used by summarizer, classifier, and HandoffManager."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        count = src.count("_system_model_call")
        assert count >= 3, (
            f"Expected at least 3 references to _system_model_call (summarizer, classifier, handoff), found {count}"
        )

    def test_conditional_degradation_pattern(self):
        """Doc: 框架通过 if _system_model_call: 模式检查."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "if _system_model_call:" in src or "if _system_model_call" in src


# ---------------------------------------------------------------------------
# 11. Cross-Document Consistency
# ---------------------------------------------------------------------------

class TestCrossDocConsistency:
    """Verify file and class existence claims."""

    def test_routing_files_exist(self):
        """Doc references these specific files."""
        root = Path(__file__).parent.parent.parent
        assert (root / "arf/routing/two_tier.py").exists()
        assert (root / "arf/core/protocols/routing.py").exists()
        assert (root / "arf/core/model_adapter.py").exists()

    def test_two_tier_router_importable(self):
        """Doc: TwoTierRouter in arf/routing/two_tier.py."""
        from arf.routing.two_tier import TwoTierRouter
        assert TwoTierRouter is not None


# ---------------------------------------------------------------------------
# 12. Thinking enabled — L3 behavior (BUG: string "false" is truthy)
# ---------------------------------------------------------------------------

class TestThinkingEnabledBug:
    """Doc 2.1/2.13: system model uses thinking_enabled=false.
    Bug: the string "false" is truthy → thinking gets ENABLED."""

    def test_thinking_disabled_string_is_truthy(self):
        """L3: verify that string "false" causes thinking to be enabled.
        BUG: base.py passes thinking_enabled as string "false" (truthy) → enabled."""
        from arf.core.model_adapter import ModelAdapter
        with patch("arf.core.model_adapter.AsyncOpenAI"):
            adapter = ModelAdapter({
                "base_url": "http://test",
                "api_key": "sk-test",
                "model_name": "test",
                "thinking_enabled": "false",
            })
        params, extra = adapter._build_api_params()
        assert "thinking" in extra, "thinking should be in extra_body"
        assert extra["thinking"]["type"] == "enabled", (
            "BUG: string 'false' is truthy, thinking got ENABLED instead of disabled"
        )

    def test_thinking_enabled_bool_false_works(self):
        """L3: verify that bool False correctly disables thinking."""
        from arf.core.model_adapter import ModelAdapter
        with patch("arf.core.model_adapter.AsyncOpenAI"):
            adapter = ModelAdapter({
                "base_url": "http://test",
                "api_key": "sk-test",
                "model_name": "test",
                "thinking_enabled": False,
            })
        params, extra = adapter._build_api_params()
        assert "thinking" in extra
        assert extra["thinking"]["type"] == "disabled", (
            "bool False should disable thinking"
        )

    def test_base_agent_uses_bool_false(self):
        """L1: base.py passes thinking_enabled as bool False (not string 'false')."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert '"thinking_enabled": False' in src, (
            "base.py should use bool False for thinking_enabled"
        )

    def test_memory_extractor_uses_bool_false(self):
        """L1: extractor.py passes thinking_enabled as bool False (not string 'false')."""
        from pathlib import Path
        root = Path(__file__).parent.parent.parent
        src = (root / "arf/plugins/memory/tools/memory_extract/extractor.py").read_text()
        assert '"thinking_enabled": False' in src, (
            "extractor.py should use bool False for thinking_enabled"
        )


# ---------------------------------------------------------------------------
# 13. Stream event field names vs doc (doc says reasoning_content, code uses reasoning)
# ---------------------------------------------------------------------------

class TestStreamEventFieldNames:
    """Doc 2.10: stream events table."""

    def test_chunk_event_uses_reasoning_not_reasoning_content(self):
        """Doc says 'reasoning_content' but code uses 'reasoning' field.
        The doc table (line 201) says reasoning_content but the code emits reasoning."""
        import inspect as _ins
        from arf.core.model_adapter import ModelAdapter
        src = _ins.getsource(ModelAdapter.chat_stream_full)
        assert '"reasoning"' in src, "code emits 'reasoning' field"
        # The doc says reasoning_content — this is the delta attr name, not the event key
        assert 'reasoning_content' in src, "delta attribute is reasoning_content"


# ---------------------------------------------------------------------------
# 14. HandoffManager LLM usage (doc 2.13)
# ---------------------------------------------------------------------------

class TestHandoffLLMClassification:
    """Doc 2.13: HandoffManager uses LLM to resolve handoff targets."""

    def test_handoff_manager_accepts_system_model_call(self):
        """Doc: HandoffManager accepts system_model_call parameter."""
        import inspect as _ins
        from arf.engine.handoff import HandoffManager
        sig = _ins.signature(HandoffManager.__init__)
        assert "system_model_call" in sig.parameters

    def test_handoff_uses_llm_for_multi_candidate(self):
        """Doc: multiple candidates → LLM matches trigger against task."""
        import inspect as _ins
        from arf.engine.handoff import HandoffManager
        src = _ins.getsource(HandoffManager.resolve)
        assert "_system_model_call" in src, "resolve() should use LLM for multi-candidate"

    def test_handoff_falls_back_to_keyword_match(self):
        """Doc: LLM unavailable → falls back to trigger keyword match."""
        import inspect as _ins
        from arf.engine.handoff import HandoffManager
        src = _ins.getsource(HandoffManager.resolve)
        assert "trigger.split" in src, "should fall back to keyword trigger match"

    def test_handoff_detect_doc_claim(self):
        """Doc 2.13 table: handoff 规则仍基于 trigger 字段匹配生效."""
        import inspect as _ins
        from arf.engine.handoff import HandoffManager
        src = _ins.getsource(HandoffManager.detect)
        assert "handoff" in src.lower()


# ---------------------------------------------------------------------------
# 15. DEFAULT_WINDOW_SIZE vs ModelConfig default
# ---------------------------------------------------------------------------

class TestWindowSizeDefaults:
    """Cross-module consistency: compaction window vs model config."""

    def test_default_window_size(self):
        """Doc: compaction uses model's context_window. Default is 131072."""
        from arf.compaction.sliding_window import DEFAULT_WINDOW_SIZE
        assert DEFAULT_WINDOW_SIZE == 131_072

    def test_model_config_default_context_window(self):
        """ModelConfig default context_window matches DEFAULT_WINDOW_SIZE."""
        from arf.core.config_base import ModelConfig
        assert ModelConfig.model_fields["context_window"].default == 131_072


# ---------------------------------------------------------------------------
# 16. Doc 2.5 code snippet accuracy — missing try/except
# ---------------------------------------------------------------------------

class TestDocCodeSnippetAccuracy:
    """Doc 2.5 shows classifier code without try/except wrapper."""

    def test_classifier_has_try_except(self):
        """Doc code snippet omits try/except; actual code has it."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        # The actual _classify closure has try/except
        assert "except Exception:" in src
        assert 'return "medium"' in src
