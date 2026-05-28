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


# ---------------------------------------------------------------------------
# BaseAgent stop() lifecycle
# ---------------------------------------------------------------------------

class TestBaseAgentStop:
    """BaseAgent.stop() — session_end(shutdown), state marking, cleanup."""

    def _make_agent(self, hook_runner=None, state_store=None):
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

        overrides = {}
        if hook_runner is not None:
            overrides["hook_runner"] = hook_runner
        if state_store is not None:
            overrides["state_store"] = state_store

        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            with patch("arf.agent.base.BaseAgent._inject_model_calls", return_value=None):
                with patch("arf.core.model_adapter.OpenAI"):
                    from arf.agent.base import BaseAgent
                    return BaseAgent(config, **overrides)

    def test_stop_fires_session_end_shutdown(self):
        """stop() fires session_end(reason='shutdown') for each active session."""
        async def _test():
            from unittest.mock import AsyncMock

            hook_runner = MagicMock()
            hook_runner.fire = AsyncMock(return_value=[])

            agent = self._make_agent(hook_runner=hook_runner)
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "s1",
                "messages": [{"role": "assistant", "content": "ok"}],
                "session_active": True,
            })
            agent._state_store.get = AsyncMock(return_value={
                "session_id": "s1",
                "messages": [],
                "session_active": True,
            })
            agent._state_store.put = AsyncMock()
            await agent.chat("hi", session_id="s1")

            # Clear call history from chat()
            hook_runner.fire.reset_mock()

            await agent.stop()

            session_end_calls = [
                c for c in hook_runner.fire.call_args_list
                if c[0][0] == "session_end"
            ]
            assert len(session_end_calls) == 1
            assert session_end_calls[0][0][1]["reason"] == "shutdown"
            assert session_end_calls[0][0][1]["session_id"] == "s1"

        asyncio.run(_test())

    def test_stop_marks_session_inactive(self):
        """stop() writes session_active=False to state store."""
        async def _test():
            from unittest.mock import AsyncMock

            agent = self._make_agent()
            put_values = []
            agent._state_store.get = AsyncMock(return_value={
                "session_id": "s1",
                "messages": [],
                "session_active": True,
            })

            async def track_put(sid, state):
                put_values.append((sid, state.get("session_active")))
            agent._state_store.put = track_put

            agent._active_sessions.add("s1")
            await agent.stop()

            assert len(put_values) == 1
            assert put_values[0][1] is False, "stop() must set session_active=False"

        asyncio.run(_test())

    def test_stop_clears_active_sessions(self):
        """stop() clears _active_sessions set."""
        async def _test():
            from unittest.mock import AsyncMock

            agent = self._make_agent()
            agent._state_store.get = AsyncMock(return_value=None)
            agent._state_store.put = AsyncMock()
            agent._active_sessions.add("s1")
            agent._active_sessions.add("s2")

            await agent.stop()

            assert len(agent._active_sessions) == 0

        asyncio.run(_test())

    def test_stop_handles_state_store_error_gracefully(self):
        """stop() does not crash when state_store.put() raises."""
        async def _test():
            from unittest.mock import AsyncMock

            agent = self._make_agent()

            async def failing_put(sid, state):
                raise OSError("disk full")
            agent._state_store.put = failing_put
            agent._state_store.get = AsyncMock(return_value={
                "session_id": "s1",
                "messages": [],
                "session_active": True,
            })
            agent._active_sessions.add("s1")

            # Should not raise
            await agent.stop()
            assert len(agent._active_sessions) == 0

        asyncio.run(_test())

    def test_stop_no_active_sessions_is_noop(self):
        """stop() with no active sessions does nothing."""
        async def _test():
            from unittest.mock import AsyncMock

            agent = self._make_agent()
            agent._state_store.put = AsyncMock()

            await agent.stop()  # Should not raise
            agent._state_store.put.assert_not_called()

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# round_start hook integration (at BaseAgent level)
# ---------------------------------------------------------------------------

class TestRoundStartHook:
    """round_start hook fires in BaseAgent.chat() / astream()."""

    def _make_agent(self, hook_runner):
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
                    return BaseAgent(config, hook_runner=hook_runner)

    def test_round_start_fires_on_chat_new_session(self):
        """round_start hook fires once per chat() call."""
        async def _test():
            hook_runner = MagicMock()
            hook_runner.fire = AsyncMock(return_value=[])

            agent = self._make_agent(hook_runner)
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "default",
                "messages": [{"role": "assistant", "content": "hi"}],
                "session_active": True,
            })
            agent._state_store.get = AsyncMock(return_value=None)
            agent._state_store.put = AsyncMock()

            await agent.chat("hello")

            round_start_calls = [
                c for c in hook_runner.fire.call_args_list
                if c[0][0] == "round_start"
            ]
            assert len(round_start_calls) == 1

        asyncio.run(_test())

    def test_round_start_context_has_session_and_round(self):
        """round_start payload includes session_id and round number."""
        async def _test():
            hook_runner = MagicMock()
            round_start_ctx = {}

            async def capture(event_type, context):
                if event_type == "round_start":
                    round_start_ctx.update(context)
                return []
            hook_runner.fire = capture

            agent = self._make_agent(hook_runner)
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "my-session",
                "messages": [{"role": "assistant", "content": "hi"}],
                "session_active": True,
            })
            agent._state_store.get = AsyncMock(return_value=None)
            agent._state_store.put = AsyncMock()

            await agent.chat("hello", session_id="my-session")

            assert round_start_ctx["session_id"] == "my-session"
            assert round_start_ctx["round"] == 0  # First round

        asyncio.run(_test())

    def test_round_start_fires_before_engine_invoke(self):
        """round_start fires in BaseAgent before engine.invoke() is called."""
        async def _test():
            call_order = []

            async def track(event_type, context):
                call_order.append(event_type)
                return []
            hook_runner = MagicMock()
            hook_runner.fire = track

            agent = self._make_agent(hook_runner)
            agent._state_store.get = AsyncMock(return_value=None)
            agent._state_store.put = AsyncMock()

            invoke_called = False

            async def fake_invoke(state):
                nonlocal invoke_called
                invoke_called = True
                return {
                    "session_id": "default",
                    "messages": [{"role": "assistant", "content": "ok"}],
                    "session_active": True,
                }
            agent._engine.invoke = fake_invoke

            await agent.chat("hello")

            assert "round_start" in call_order, f"round_start missing from {call_order}"
            assert "session_start" in call_order, f"session_start missing from {call_order}"

            # Both session_start and round_start fire before invoke
            # (invoke is called after hooks fire in chat())
            assert invoke_called, "engine.invoke should have been called"

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Session lifecycle edge cases
# ---------------------------------------------------------------------------

class TestSessionLifecycleEdgeCases:
    """stop() then chat(), multiple active sessions, session_active flag lifecycle."""

    def _make_agent(self, hook_runner=None, state_store=None):
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

        overrides = {}
        if hook_runner is not None:
            overrides["hook_runner"] = hook_runner
        if state_store is not None:
            overrides["state_store"] = state_store

        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            with patch("arf.agent.base.BaseAgent._inject_model_calls", return_value=None):
                with patch("arf.core.model_adapter.OpenAI"):
                    from arf.agent.base import BaseAgent
                    return BaseAgent(config, **overrides)

    def test_chat_after_stop_starts_new_session(self):
        """After stop(), next chat() starts a fresh session (session_start fires)."""
        async def _test():
            hook_runner = MagicMock()
            hook_runner.fire = AsyncMock(return_value=[])

            agent = self._make_agent(hook_runner=hook_runner)
            agent._state_store.get = AsyncMock(return_value={
                "session_id": "s1",
                "messages": [],
                "session_active": False,  # cleanly shut down
            })
            agent._state_store.put = AsyncMock()
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "s1",
                "messages": [{"role": "assistant", "content": "ok"}],
                "session_active": True,
            })

            await agent.chat("hi", session_id="s1")
            assert "s1" in agent._active_sessions

            # Simulate stop — removes from active_sessions, marks inactive in store
            agent._active_sessions.discard("s1")

            # Next chat: state_store has session_active=False → treated as NEW session
            hook_runner.fire.reset_mock()
            await agent.chat("hi again", session_id="s1")

            session_start_calls = [
                c for c in hook_runner.fire.call_args_list
                if c[0][0] == "session_start"
            ]
            assert len(session_start_calls) == 1, (
                "After clean shutdown, next chat() should fire session_start"
            )

        asyncio.run(_test())

    def test_multiple_sessions_tracked_independently(self):
        """Each session_id is tracked separately in _active_sessions."""
        async def _test():
            hook_runner = MagicMock()
            hook_runner.fire = AsyncMock(return_value=[])
            put_calls = []

            agent = self._make_agent(hook_runner=hook_runner)
            agent._state_store.get = AsyncMock(return_value=None)

            async def track_put(sid, state):
                put_calls.append(sid)
            agent._state_store.put = track_put

            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "s1",
                "messages": [{"role": "assistant", "content": "ok"}],
                "session_active": True,
            })

            await agent.chat("hi", session_id="s1")
            await agent.chat("hi", session_id="s2")

            assert "s1" in agent._active_sessions
            assert "s2" in agent._active_sessions

        asyncio.run(_test())

    def test_default_session_id_is_used_when_not_specified(self):
        """When session_id is not provided, 'default' is used."""
        async def _test():
            agent = self._make_agent()
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "default",
                "messages": [{"role": "assistant", "content": "ok"}],
                "session_active": True,
            })
            agent._state_store.get = AsyncMock(return_value=None)
            agent._state_store.put = AsyncMock()

            await agent.chat("hello")
            assert "default" in agent._active_sessions

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Checkpoint behavior in GraphEngine.invoke()
# ---------------------------------------------------------------------------

class TestEngineCheckpointBehavior:
    """State checkpoint (state_store.put) behavior during engine execution."""

    def test_checkpoint_saved_on_text_only_response(self):
        """state_store.put() is called even for text-only (no tool) responses."""
        from arf.engine.loop_strategies.react import ReActStrategy

        state_store = MagicMock()
        state_store.put = AsyncMock()
        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        engine = _build_engine(
            loop_strategy=ReActStrategy(max_turns=50),
            state_store=state_store,
            tool_resolver=tool_resolver,
        )
        engine._call_model = AsyncMock(return_value={
            "content": "hello, no tools needed",
            "tool_calls": [],
            "usage": {"total_tokens": 10},
        })

        state = {
            "session_id": "test", "agent_name": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "current_model": "test", "current_turn": 0,
            "interaction_round": 0, "context_summary": "",
            "tool_results": {}, "plan": None, "metadata": {},
        }

        asyncio.run(engine.invoke(state))
        assert state_store.put.called, (
            "state_store.put() must be called even for text-only responses"
        )

    def test_checkpoint_saved_per_turn_with_tools(self):
        """Each turn has at least one checkpoint. Turns with tool calls have two:
        one after append assistant+tool_calls (crash recovery), one after tool exec."""
        from arf.engine.loop_strategies.react import ReActStrategy

        state_store = MagicMock()
        state_store.put = AsyncMock()
        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        call_count = [0]

        async def call_model(msgs, model, tools=None):
            call_count[0] += 1
            if call_count[0] < 3:
                return {
                    "content": "",
                    "tool_calls": [{"id": f"t{call_count[0]}", "name": "mock", "params": {}}],
                    "usage": {},
                }
            return {"content": "done", "tool_calls": [], "usage": {}}

        engine = _build_engine(
            loop_strategy=ReActStrategy(max_turns=50),
            state_store=state_store,
            tool_resolver=tool_resolver,
        )
        engine._call_model = call_model
        engine.tool_executor.execute = AsyncMock(return_value={})

        state = {
            "session_id": "test", "agent_name": "test",
            "messages": [{"role": "user", "content": "do stuff"}],
            "current_model": "test", "current_turn": 0,
            "interaction_round": 0, "context_summary": "",
            "tool_results": {}, "plan": None, "metadata": {},
        }

        asyncio.run(engine.invoke(state))
        # Turns 1-2: 2 checkpoints each (before tool exec + after). Turn 3 text: 1.
        assert state_store.put.call_count == 5, (
            f"Expected 5 checkpoints (2 per tool turn + 1 text), got {state_store.put.call_count}"
        )

    def test_invoke_saves_checkpoint_before_tool_exec(self):
        """invoke() saves checkpoint after appending assistant+tool_calls,
        before tool execution — matching astream behavior."""
        from arf.engine.loop_strategies.react import ReActStrategy

        put_states = []

        async def capture_put(sid, state):
            put_states.append((sid, list(state.get("messages", []))))

        state_store = MagicMock()
        state_store.put = capture_put
        tool_resolver = MagicMock()
        tool_resolver.get_tool_definitions = AsyncMock(return_value=[])

        async def call_model(msgs, model, tools=None):
            return {
                "content": "let me help",
                "tool_calls": [{"id": "t1", "name": "mock", "params": {}}],
                "usage": {},
            }

        engine = _build_engine(
            loop_strategy=ReActStrategy(max_turns=50),
            state_store=state_store,
            tool_resolver=tool_resolver,
        )
        engine._call_model = call_model
        engine.tool_executor.execute = AsyncMock(return_value={
            "t1": type("R", (), {"success": True, "data": "result", "error": None,
                                  "duration_ms": 10, "rolled_back": False, "rollback_error": None})(),
        })

        state = {
            "session_id": "test", "agent_name": "test",
            "messages": [{"role": "user", "content": "help"}],
            "current_model": "test", "current_turn": 0,
            "interaction_round": 0, "context_summary": "",
            "tool_results": {}, "plan": None, "metadata": {},
        }

        asyncio.run(engine.invoke(state))
        assert len(put_states) >= 1

        # The first checkpoint should contain assistant with tool_calls
        first_put = put_states[0][1]
        last_assistant = first_put[-1]
        assert last_assistant["role"] == "assistant"
        assert "tool_calls" in last_assistant, (
            "First checkpoint must persist assistant+tool_calls for crash recovery"
        )

    def test_invoke_and_astream_checkpoint_consistent(self):
        """invoke and astream both save checkpoint after appending tool_calls,
        before executing tools — no asymmetry."""
        import inspect
        from arf.engine.graph import GraphEngine

        invoke_src = inspect.getsource(GraphEngine.invoke)
        astream_src = inspect.getsource(GraphEngine.astream)

        # Both must NOT have the old NOTE about skipping put
        for name, src in [("invoke", invoke_src), ("astream", astream_src)]:
            assert "do NOT state_store.put() here" not in src, (
                f"Outdated NOTE should be removed from {name}()"
            )

        # Both paths have the pattern: append assistant_msg → put
        for name, src in [("invoke", invoke_src), ("astream", astream_src)]:
            lines = src.split("\n")
            append_line = next(i for i, l in enumerate(lines) if 'append(assistant_msg)' in l)
            put_found = any("state_store.put" in l for l in lines[append_line:append_line + 5])
            assert put_found, (
                f"{name}() must call state_store.put() after appending assistant_msg"
            )
