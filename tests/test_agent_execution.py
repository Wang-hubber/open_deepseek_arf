"""Tests for Agent execution — hooks, lifecycle, turn reset, circuit breakers."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _build_engine(**overrides):
    """Build a minimal GraphEngine with mock dependencies."""
    from arf.engine.graph import GraphEngine

    defaults = {
        "loop_strategy": MagicMock(),
        "state_store": MagicMock(),
        "tool_executor": MagicMock(),
        "tool_resolver": MagicMock(),
        "error_policy": None,
        "model_router": None,
        "event_bus": None,
        "system_prompt": "",
    }
    defaults.update(overrides)
    return GraphEngine(**defaults)


class TestRoundHooks:
    """round_start / round_end hook events.

    round_start fires in BaseAgent (tested at integration level).
    round_end fires in GraphEngine (tested below).
    """

    def _state(self):
        return {
            "session_id": "test",
            "agent_name": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "current_model": "test",
            "current_turn": 0,
            "interaction_round": 0,
            "context_summary": "",
            "tool_results": {},
            "plan": None,
            "metadata": {},
        }

    def _make_engine(self, hook_runner):
        """Build engine with mocks that survive the full invoke() cycle."""
        loop_strategy = MagicMock()
        loop_strategy.should_continue.side_effect = [True, False]

        state_store = MagicMock()
        state_store.get = AsyncMock(return_value=None)
        state_store.put = AsyncMock()

        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        engine = _build_engine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            hook_runner=hook_runner,
            tool_resolver=tool_resolver,
        )
        engine._call_model = AsyncMock(return_value={
            "content": "hello", "tool_calls": [], "usage": {"total_tokens": 10},
        })
        return engine

    def test_round_end_hook_fired_in_engine(self):
        """round_end hook fires in invoke() after while loop ends."""
        hook_runner = MagicMock()
        hook_runner.fire = AsyncMock(return_value=[])

        engine = self._make_engine(hook_runner)
        asyncio.run(engine.invoke(self._state()))

        round_end_calls = [
            c for c in hook_runner.fire.call_args_list
            if c[0][0] == "round_end"
        ]
        assert len(round_end_calls) == 1, "round_end hook should fire once"

    def test_round_end_fires_as_last_engine_hook(self):
        """round_end fires after loop ends; session_end no longer fires from engine."""
        call_order = []
        hook_runner = MagicMock()

        async def track_fire(event_type, context):
            call_order.append(event_type)
            return []
        hook_runner.fire = track_fire

        engine = self._make_engine(hook_runner)
        asyncio.run(engine.invoke(self._state()))

        assert "round_end" in call_order, f"Expected round_end in call_order: {call_order}"
        assert "session_end" not in call_order, (
            f"session_end hook fire should be removed from engine; got {call_order}"
        )
        assert call_order[-1] == "round_end", (
            f"round_end should be last hook event, got {call_order}"
        )


class TestSessionHooksRemovedFromEngine:
    """session_start/session_end no longer fire from GraphEngine."""

    def test_no_session_start_in_invoke(self):
        """GraphEngine.invoke() must not fire session_start hook."""
        import inspect
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert 'fire("session_start"' not in src, (
            "session_start hook fire should be removed from GraphEngine.invoke()"
        )

    def test_no_session_end_in_invoke(self):
        """GraphEngine.invoke() must not fire session_end hook."""
        import inspect
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.invoke)
        assert 'fire("session_end"' not in src, (
            "session_end hook fire should be removed from GraphEngine.invoke()"
        )

    def test_no_session_start_in_astream(self):
        """GraphEngine.astream() must not fire session_start hook."""
        import inspect
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.astream)
        assert 'fire("session_start"' not in src, (
            "session_start hook fire should be removed from GraphEngine.astream()"
        )

    def test_no_session_end_in_astream(self):
        """GraphEngine.astream() must not fire session_end hook."""
        import inspect
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine.astream)
        assert 'fire("session_end"' not in src, (
            "session_end hook fire should be removed from GraphEngine.astream()"
        )


class TestSessionHooks:
    """session_start / session_end at correct lifecycle boundaries."""

    def _make_agent(self, hook_runner):
        """Build a BaseAgent with mocked engine and state_store."""
        from arf.core.config_base import ModelConfig
        from arf.agent.config import AgentConfig

        config = AgentConfig(
            name="test",
            models=[ModelConfig(
                type="quick", model="test-model",
                api_base="https://api.example.com",
                api_key_env="TEST_KEY", context_window=128000,
            )],
        )

        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            with patch("arf.agent.base.BaseAgent._inject_model_calls", return_value=None):
                with patch("arf.core.model_adapter.OpenAI"):
                    from arf.agent.base import BaseAgent
                    agent = BaseAgent(
                        config,
                        hook_runner=hook_runner,
                        state_store=MagicMock(),
                        memory_store=MagicMock(),
                    )
        return agent

    def test_session_start_on_new_session(self):
        """session_start fires once when state_store is empty."""
        async def _test():
            hook_runner = MagicMock()
            hook_runner.fire = AsyncMock(return_value=[])

            agent = self._make_agent(hook_runner)
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "default",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
                "session_active": True,
            })
            agent._state_store.get = AsyncMock(return_value=None)
            agent._state_store.put = AsyncMock()

            await agent.chat("hi")

            session_start_calls = [
                c for c in hook_runner.fire.call_args_list
                if c[0][0] == "session_start"
            ]
            assert len(session_start_calls) == 1, "session_start should fire once on new session"

        asyncio.run(_test())

    def test_no_session_start_on_active_session(self):
        """session_start does NOT fire when session is already active."""
        async def _test():
            hook_runner = MagicMock()
            hook_runner.fire = AsyncMock(return_value=[])

            agent = self._make_agent(hook_runner)
            agent._active_sessions.add("default")  # mark as already running
            agent._state_store.get = AsyncMock(return_value={
                "session_id": "default",
                "messages": [{"role": "user", "content": "previous"}],
                "session_active": True,
            })
            agent._state_store.put = AsyncMock()
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "default",
                "messages": [],
                "session_active": True,
            })

            await agent.chat("hi again")

            session_start_calls = [
                c for c in hook_runner.fire.call_args_list
                if c[0][0] == "session_start"
            ]
            assert len(session_start_calls) == 0, (
                "session_start should NOT fire on already-active session"
            )

        asyncio.run(_test())

    def test_crash_recovery_fires_session_end(self):
        """If previous state was active (crash), fire session_end(recovery) before session_start."""
        async def _test():
            call_order = []
            hook_runner = MagicMock()

            async def track(event_type, context):
                call_order.append((event_type, context))
                return []
            hook_runner.fire = track

            agent = self._make_agent(hook_runner)
            agent._state_store.get = AsyncMock(return_value={
                "session_id": "default",
                "messages": [{"role": "user", "content": "unfinished"}],
                "session_active": True,
            })
            agent._state_store.put = AsyncMock()
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "default",
                "messages": [],
                "session_active": True,
            })

            await agent.chat("hi after crash")

            session_end_idx = -1
            session_start_idx = -1
            for i, (et, ctx) in enumerate(call_order):
                if et == "session_end":
                    session_end_idx = i
                if et == "session_start":
                    session_start_idx = i

            assert session_end_idx >= 0, "session_end(recovery) should fire after crash"
            assert session_start_idx >= 0, "session_start should fire"
            assert session_end_idx < session_start_idx, (
                f"session_end(recovery) at {session_end_idx} must fire before "
                f"session_start at {session_start_idx}"
            )

        asyncio.run(_test())

    def test_old_state_format_treated_as_new(self):
        """State without session_active field is treated as new session."""
        async def _test():
            hook_runner = MagicMock()
            hook_runner.fire = AsyncMock(return_value=[])

            agent = self._make_agent(hook_runner)
            agent._state_store.get = AsyncMock(return_value={
                "session_id": "default",
                "messages": [{"role": "user", "content": "old format"}],
            })
            agent._state_store.put = AsyncMock()
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "default",
                "messages": [],
                "session_active": True,
            })

            await agent.chat("hi")

            session_start_calls = [
                c for c in hook_runner.fire.call_args_list
                if c[0][0] == "session_start"
            ]
            assert len(session_start_calls) == 1, "Old format should trigger session_start"

        asyncio.run(_test())


class TestTurnReset:
    """Per-round turn reset and max_turns circuit breaker."""

    def test_turn_resets_to_zero_each_round(self):
        """Engine receives current_turn=0 at the start of every round."""
        from arf.engine.loop_strategies.react import ReActStrategy

        state_store = MagicMock()
        state_store.get = AsyncMock(return_value={
            "session_id": "default",
            "messages": [],
            "current_turn": 15,
            "session_active": True,
            "interaction_round": 5,
        })
        state_store.put = AsyncMock()
        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        async def call_model(msgs, model, tools=None):
            return {"content": "hi back", "tool_calls": [], "usage": {}}

        engine = _build_engine(
            loop_strategy=ReActStrategy(max_turns=50),
            state_store=state_store,
            tool_resolver=tool_resolver,
        )
        engine._call_model = call_model

        state = {
            "session_id": "default",
            "agent_name": "test",
            "messages": [{"role": "user", "content": "new round"}],
            "current_model": "test",
            "current_turn": 0,  # chat() always resets this to 0
            "interaction_round": 6,
            "context_summary": "",
            "tool_results": {},
            "plan": None,
            "metadata": {},
        }

        result = asyncio.run(engine.invoke(state))
        assert result["current_turn"] == 1, (
            f"Turn should start from 0 each round, got {result['current_turn']}"
        )

    def test_max_turns_stops_round(self):
        """max_turns=3 circuit breaker allows exactly 3 model calls then stops."""
        from arf.engine.loop_strategies.react import ReActStrategy

        loop_strategy = ReActStrategy(max_turns=3)
        state_store = MagicMock()
        state_store.put = AsyncMock()
        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        call_count = 0

        async def call_model(msgs, model, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return {"content": "", "tool_calls": [
                    {"id": f"t{call_count}", "name": "mock_tool", "params": {}}
                ], "usage": {}}
            return {"content": "done", "tool_calls": [], "usage": {}}

        engine = _build_engine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            tool_resolver=tool_resolver,
        )
        engine._call_model = call_model
        engine.tool_executor.execute = AsyncMock(return_value={})

        state = {
            "session_id": "test",
            "agent_name": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "current_model": "test",
            "current_turn": 0,
            "interaction_round": 0,
            "context_summary": "",
            "tool_results": {},
            "plan": None,
            "metadata": {},
        }

        asyncio.run(engine.invoke(state))
        assert call_count == 3, f"max_turns=3 should allow 3 calls, got {call_count}"

    def test_round_2_continues_after_max_turns_in_round_1(self):
        """Round 2 starts fresh even after Round 1 hit max_turns."""
        from arf.engine.loop_strategies.react import ReActStrategy

        state_store = MagicMock()
        state_store.put = AsyncMock()
        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        async def call_model(msgs, model, tools=None):
            return {"content": "done", "tool_calls": [], "usage": {}}

        # Round 1: max_turns=1
        engine_r1 = _build_engine(
            loop_strategy=ReActStrategy(max_turns=1),
            state_store=state_store,
            tool_resolver=tool_resolver,
        )
        engine_r1._call_model = call_model

        r1 = asyncio.run(engine_r1.invoke({
            "session_id": "test", "agent_name": "test",
            "messages": [{"role": "user", "content": "round1"}],
            "current_model": "test", "current_turn": 0,
            "interaction_round": 0, "context_summary": "",
            "tool_results": {}, "plan": None, "metadata": {},
        }))
        assert r1["current_turn"] == 1

        # Round 2: fresh state, turn=0, max_turns=1 should allow 1 more call
        engine_r2 = _build_engine(
            loop_strategy=ReActStrategy(max_turns=1),
            state_store=state_store,
            tool_resolver=tool_resolver,
        )
        engine_r2._call_model = call_model

        r2 = asyncio.run(engine_r2.invoke({
            "session_id": "test", "agent_name": "test",
            "messages": [
                {"role": "user", "content": "round1"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "round2"},
            ],
            "current_model": "test", "current_turn": 0,
            "interaction_round": 1, "context_summary": "",
            "tool_results": {}, "plan": None, "metadata": {},
        }))
        assert r2["current_turn"] == 1, (
            f"Round 2 should also run 1 turn, got {r2['current_turn']}"
        )

    def test_engine_fires_round_end_even_when_max_turns_reached(self):
        """round_end hook fires even when max_turns terminates the loop."""
        hook_runner = MagicMock()
        hook_runner.fire = AsyncMock(return_value=[])

        turn_count = [0]

        def should_continue(state):
            turn_count[0] += 1
            return turn_count[0] <= 3

        loop_strategy = MagicMock()
        loop_strategy.should_continue = should_continue

        state_store = MagicMock()
        state_store.put = AsyncMock()
        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        engine = _build_engine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            tool_resolver=tool_resolver,
            hook_runner=hook_runner,
        )
        engine._call_model = AsyncMock(return_value={
            "content": "ok", "tool_calls": [], "usage": {},
        })

        state = {
            "session_id": "test", "agent_name": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "current_model": "test", "current_turn": 0,
            "interaction_round": 0, "context_summary": "",
            "tool_results": {}, "plan": None, "metadata": {},
        }

        asyncio.run(engine.invoke(state))

        round_end_calls = [
            c for c in hook_runner.fire.call_args_list
            if c[0][0] == "round_end"
        ]
        assert len(round_end_calls) == 1, (
            "round_end should fire even when loop exits due to max_turns"
        )
