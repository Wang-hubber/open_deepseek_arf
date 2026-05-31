"""Tests for ARF engine recovery mechanism — continuation, compaction, backoff."""
import pytest
from arf.core.results import RecoveryState, RecoveryDecision


class TestRecoveryState:
    def test_default_all_zero(self):
        rs = RecoveryState()
        assert rs.continuation_attempts == 0
        assert rs.compact_attempts == 0
        assert rs.transport_attempts == 0

    def test_can_increment_independently(self):
        rs = RecoveryState()
        rs.continuation_attempts += 1
        rs.transport_attempts += 2
        assert rs.continuation_attempts == 1
        assert rs.compact_attempts == 0
        assert rs.transport_attempts == 2


class TestRecoveryDecision:
    def test_continue_decision(self):
        d = RecoveryDecision(kind="continue", reason="truncated")
        assert d.kind == "continue"
        assert d.reason == "truncated"

    def test_fail_decision(self):
        d = RecoveryDecision(kind="fail", reason="unknown")
        assert d.kind == "fail"


class TestRecoveryConfig:
    def test_default_budgets(self):
        from arf.agent.config import AdvancedConfig
        cfg = AdvancedConfig.default()
        rc = cfg.recovery
        assert rc is not None
        assert rc.max_continuation == 3
        assert rc.max_compaction == 3
        assert rc.max_transport_retry == 3
        assert rc.backoff_base == 1.0
        assert rc.backoff_max == 30.0

    def test_recovery_survives_roundtrip(self):
        import yaml
        from arf.agent.config import AdvancedConfig
        cfg = AdvancedConfig.default()
        cfg.recovery.max_continuation = 5
        data = cfg.model_dump(exclude_none=True)
        restored = AdvancedConfig(**data)
        assert restored.recovery.max_continuation == 5


class TestChooseRecovery:
    @staticmethod
    def _make_engine():
        from arf.engine.graph import GraphEngine
        from arf.agent.config import RecoveryConfig
        engine = GraphEngine.__new__(GraphEngine)
        engine._recovery_config = RecoveryConfig()
        return engine

    def test_max_tokens_stop_reason(self):
        engine = self._make_engine()
        d = engine._choose_recovery("length", None)
        assert d.kind == "continue"

    def test_context_overflow_error(self):
        engine = self._make_engine()
        d = engine._choose_recovery(None, "prompt too long for context window")
        assert d.kind == "compact"

    def test_context_exceeded_error(self):
        engine = self._make_engine()
        d = engine._choose_recovery(None, "context length exceeded limit")
        assert d.kind == "compact"

    def test_transient_timeout_error(self):
        engine = self._make_engine()
        d = engine._choose_recovery(None, "request timed out")
        assert d.kind == "backoff"

    def test_transient_rate_limit_error(self):
        engine = self._make_engine()
        d = engine._choose_recovery(None, "rate limit exceeded")
        assert d.kind == "backoff"

    def test_transient_unavailable_error(self):
        engine = self._make_engine()
        d = engine._choose_recovery(None, "service unavailable")
        assert d.kind == "backoff"

    def test_transient_connection_error(self):
        engine = self._make_engine()
        d = engine._choose_recovery(None, "connection refused")
        assert d.kind == "backoff"

    def test_unknown_error_returns_fail(self):
        engine = self._make_engine()
        d = engine._choose_recovery(None, "something completely unexpected")
        assert d.kind == "fail"

    def test_both_none_returns_fail(self):
        engine = self._make_engine()
        d = engine._choose_recovery(None, None)
        assert d.kind == "fail"


class TestApplyRecovery:
    @staticmethod
    def _make_engine():
        from arf.engine.graph import GraphEngine
        from arf.agent.config import RecoveryConfig
        engine = GraphEngine.__new__(GraphEngine)
        engine._recovery_config = RecoveryConfig()
        engine.compaction = None
        return engine

    @pytest.mark.anyio
    async def test_continue_appends_message(self):
        engine = self._make_engine()
        state = {}
        msgs = [{"role": "user", "content": "hello"}]
        decision = RecoveryDecision(kind="continue", reason="test")
        state, msgs, should = await engine._apply_recovery(decision, state, msgs)
        assert should is True
        assert msgs[-1]["role"] == "user"
        assert "continue directly" in msgs[-1]["content"].lower()
        assert state["_recovery_state"]["continuation_attempts"] == 1

    @pytest.mark.anyio
    async def test_continue_budget_exhausted_raises(self):
        engine = self._make_engine()
        engine._recovery_config.max_continuation = 1
        state = {"_recovery_state": {"continuation_attempts": 1, "compact_attempts": 0, "transport_attempts": 0}}
        msgs = [{"role": "user", "content": "hello"}]
        decision = RecoveryDecision(kind="continue", reason="test")
        with pytest.raises(RuntimeError, match="max continuation"):
            await engine._apply_recovery(decision, state, msgs)

    @pytest.mark.anyio
    async def test_backoff_increments_and_sleeps(self):
        engine = self._make_engine()
        state = {}
        msgs = [{"role": "user", "content": "hello"}]
        decision = RecoveryDecision(kind="backoff", reason="test")
        state, msgs, should = await engine._apply_recovery(decision, state, msgs)
        assert should is True
        assert state["_recovery_state"]["transport_attempts"] == 1

    @pytest.mark.anyio
    async def test_backoff_budget_exhausted_raises_original_error(self):
        engine = self._make_engine()
        engine._recovery_config.max_transport_retry = 1
        state = {"_recovery_state": {"continuation_attempts": 0, "compact_attempts": 0, "transport_attempts": 1}}
        msgs = [{"role": "user", "content": "hello"}]
        decision = RecoveryDecision(kind="backoff", reason="test")
        error = RuntimeError("timeout")
        with pytest.raises(RuntimeError, match="timeout"):
            await engine._apply_recovery(decision, state, msgs, error=error)

    @pytest.mark.anyio
    async def test_compact_calls_compaction(self):
        class FakeCompactor:
            def __init__(self):
                self.compacted = False

            def should_compact(self, state, **kw):
                return False

            async def compact(self, state):
                self.compacted = True
                state["_compacted"] = True
                return state

        engine = self._make_engine()
        fake = FakeCompactor()
        engine.compaction = fake
        engine._repair_messages = lambda s: s
        state = {}
        msgs = [{"role": "user", "content": "hello"}]
        decision = RecoveryDecision(kind="compact", reason="test")
        state, msgs, should = await engine._apply_recovery(decision, state, msgs)
        assert should is True
        assert fake.compacted
        assert state.get("_compacted")

    @pytest.mark.anyio
    async def test_fail_returns_false(self):
        engine = self._make_engine()
        state = {}
        msgs = []
        decision = RecoveryDecision(kind="fail", reason="test")
        state, msgs, should = await engine._apply_recovery(decision, state, msgs)
        assert should is False


class TestResetRecoveryState:
    def test_all_counters_reset(self):
        from arf.engine.graph import GraphEngine
        engine = GraphEngine.__new__(GraphEngine)
        state = {"_recovery_state": {"continuation_attempts": 5, "compact_attempts": 2, "transport_attempts": 3}}
        engine._reset_recovery_state(state)
        assert state["_recovery_state"] == {"continuation_attempts": 0, "compact_attempts": 0, "transport_attempts": 0}


class TestBackoffDelay:
    def test_increasing_delays(self):
        from arf.engine.graph import _backoff_delay
        d1 = _backoff_delay(1, base=1.0)
        d2 = _backoff_delay(2, base=1.0)
        assert d2 > d1

    def test_capped_at_max(self):
        from arf.engine.graph import _backoff_delay
        for _ in range(10):
            d = _backoff_delay(10, base=1.0, max_delay=2.0)
            assert d <= 3.0  # max 2.0 + jitter < 1.0


class TestRecoveryIntegration:
    """Full flow integration tests using mock _call_model."""

    @staticmethod
    def _make_mock_engine():
        from arf.engine.graph import GraphEngine
        from arf.agent.config import RecoveryConfig
        engine = GraphEngine.__new__(GraphEngine)
        engine._recovery_config = RecoveryConfig()
        engine.compaction = None
        engine.error_policy = None
        engine.model_router = None
        engine.guard_runner = None
        engine.hook_runner = None
        engine._max_turns = 3
        engine._interaction_round = 0
        engine._system_prompt = ""
        engine.event_bus = None
        engine.tool_resolver = None
        engine._repair_messages = lambda s: s
        engine._pars_tool_calls = lambda r: []
        engine._last_user_message = lambda s: ""

        class MockLoopStrategy:
            def __init__(self, max_steps=3):
                self._max = max_steps
                self._count = 0
            def should_continue(self, state):
                self._count += 1
                return self._count <= self._max
            def next_step(self, state):
                return "call_model"

        class MockStateStore:
            async def get(self, sid):
                return None
            async def put(self, sid, state):
                pass

        engine.loop_strategy = MockLoopStrategy(max_steps=3)
        engine.state_store = MockStateStore()
        engine._cancel_event = None
        engine._emit = lambda *a, **kw: None
        return engine

    @pytest.mark.anyio
    async def test_continuation_triggered_on_length_finish_reason(self):
        """Full flow: mock _call_model returns finish_reason='length' -> continuation triggered."""
        engine = self._make_mock_engine()

        call_count = [0]
        async def mock_call_model(msgs, model, tools=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "content": "Let me help with that",
                    "tool_calls": [],
                    "usage": {"total_tokens": 1000},
                    "finish_reason": "length",
                }
            return {
                "content": "continuing from where I stopped",
                "tool_calls": [],
                "usage": {"total_tokens": 500},
                "finish_reason": "stop",
            }

        engine._call_model = mock_call_model

        state = {
            "messages": [{"role": "user", "content": "hello"}],
            "session_id": "test",
            "interaction_round": 0,
            "current_model": "test-model",
        }

        result = await engine.invoke(state)
        assert call_count[0] == 2  # first call got length, continuation triggered
        msgs = result.get("messages", [])
        assert any("continue directly" in m.get("content", "").lower() for m in msgs)

    @pytest.mark.anyio
    async def test_backoff_retries_then_succeeds(self):
        """Mock _call_model raises timeout twice, backoff retries, then succeeds."""
        engine = self._make_mock_engine()

        call_count = [0]
        async def mock_call_model(msgs, model, tools=None):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("connection timed out")
            return {
                "content": "finally worked",
                "tool_calls": [],
                "usage": {"total_tokens": 100},
                "finish_reason": "stop",
            }

        engine._call_model = mock_call_model

        state = {
            "messages": [{"role": "user", "content": "hello"}],
            "session_id": "test",
            "interaction_round": 0,
            "current_model": "test-model",
        }

        result = await engine.invoke(state)
        assert call_count[0] == 3  # 2 failures + 1 success
        assert result["_recovery_state"]["transport_attempts"] == 2


class TestFinishReasonPropagation:
    def test_model_adapter_sets_finish_reason(self):
        from unittest.mock import MagicMock, patch
        from arf.core.model_adapter import ModelAdapter

        fake_msg = MagicMock()
        fake_msg.content = "hello"
        fake_msg.tool_calls = None
        fake_msg.reasoning_content = None
        fake_choice = MagicMock()
        fake_choice.message = fake_msg
        fake_choice.finish_reason = "stop"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]
        fake_response.usage = None

        with patch("arf.core.model_adapter.OpenAI"):
            adapter = ModelAdapter(
                config={
                    "model_name": "test-model",
                    "api_key": "placeholder",
                    "base_url": "https://test.example.com",
                },
            )
            with patch.object(adapter, "_call_with_retry", return_value=fake_response):
                result = adapter.chat_complete([{"role": "user", "content": "hi"}])
                assert result.finish_reason == "stop"
