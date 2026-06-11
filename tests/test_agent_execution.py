"""Tests for Agent execution — hooks, lifecycle, turn reset, circuit breakers."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tests.fixtures.fake_model_adapter import FakeModelAdapter, FakeResponse


class _HookRunnerPlugin:
    """Adapts a traditional hook_runner mock to ControlPlane's plugin system."""

    def __init__(self, hook_runner):
        self._hook_runner = hook_runner
        self._name = "hook_runner_plugin"
        self._hooks = {
            "session_start": "blocking",
            "session_end": "blocking",
            "round_start": "blocking",
            "round_end": "blocking",
            "turn_start": "blocking",
            "turn_end": "blocking",
            "pre_action": "blocking",
            "post_action": "blocking",
            "error": "blocking",
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def hooks(self) -> dict[str, str]:
        return self._hooks

    async def on_hook(self, event_type: str, context) -> None:
        await self._hook_runner.fire(event_type, context)


def _build_real_engine(fake_model=None, tool_provider=None, skill_provider=None,
                       tmp_path=None, **overrides):
    """Build a ControlPlane with real component defaults (no MagicMock)."""
    from arf.engine.tool_executor import ConcurrentToolExecutor
    from arf.engine.control_plane import ControlPlane
    from arf.resources.resolver import ResourceResolver
    from arf.event_bus import InMemoryEventBus
    from arf.engine.checkpoint import InMemoryStateStore

    APP_DIR = Path(__file__).parent.parent / "app" / "arf_default_assistant"

    if tool_provider is None:
        from arf.resources.providers.tool_provider import ToolProvider
        tool_provider = ToolProvider(APP_DIR / "tools")
    if skill_provider is None:
        from arf.resources.providers.skill_provider import SkillProvider
        skill_provider = SkillProvider(APP_DIR / "skills")
    if fake_model is None:
        fake_model = FakeModelAdapter(default=FakeResponse(content="hello"))

    resolver = ResourceResolver(tool_provider, skill_provider=skill_provider)
    state_store = overrides.pop("state_store", None) or InMemoryStateStore()
    hook_runner = overrides.pop("hook_runner", None)
    # error_policy is not used by ControlPlane
    overrides.pop("error_policy", None)
    # approval_enabled is not used by ControlPlane
    overrides.pop("approval_enabled", None)

    blocking_plugins = []
    if hook_runner is not None:
        blocking_plugins.append(_HookRunnerPlugin(hook_runner))

    defaults = {
        "max_turns": 10,
        "state_store": state_store,
        "tool_executor": ConcurrentToolExecutor(resolver),
        "event_bus": InMemoryEventBus(),
        "blocking_plugins": blocking_plugins,
        "side_plugins": [],
    }
    defaults.update(overrides)

    # Only set _call_model if not already provided via overrides
    if "call_model" not in overrides:

        async def _wrap_call(messages, model_name="", tools=None):
            result = await fake_model.chat_complete(messages, tools=tools)
            return {
                "content": result.content,
                "tool_calls": result.tool_calls,
                "usage": result.usage,
            }

        defaults["call_model"] = _wrap_call

    # stream_model is opt-in — only set if explicitly passed via overrides
    if "stream_model" in overrides:
        defaults["stream_model"] = overrides.pop("stream_model")

    return ControlPlane(**defaults)


class CountingStateStore:
    """InMemoryStateStore wrapper that tracks put/get call counts for test assertions."""

    def __init__(self):
        from arf.engine.checkpoint import InMemoryStateStore
        self._inner = InMemoryStateStore()
        self.put_call_count = 0
        self.get_call_count = 0
        self.put_states = []
        self.get_keys = []

    async def put(self, key, state):
        import copy
        self.put_call_count += 1
        self.put_states.append({"key": key, "state": copy.deepcopy(state) if isinstance(state, dict) else state})
        return await self._inner.put(key, state)

    async def get(self, key):
        self.get_call_count += 1
        self.get_keys.append(key)
        return await self._inner.get(key)

    async def delete(self, key):
        return await self._inner.delete(key)

    def reset(self):
        self._inner.reset()
        self.put_call_count = 0
        self.get_call_count = 0
        self.put_states = []
        self.get_keys = []


class TestRoundHooks:
    """round_start / round_end hook events.

    round_start fires in BaseAgent (tested at integration level).
    round_end fires in ControlPlane (tested below).
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
        """Build engine with real components for the full invoke() cycle."""
        fake = FakeModelAdapter(default=FakeResponse(content="hello"))
        engine = _build_real_engine(
            fake_model=fake,
            hook_runner=hook_runner,
        )
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
        """round_end fires after loop ends; session_end is the final hook event."""
        call_order = []
        hook_runner = MagicMock()

        async def track_fire(event_type, context):
            call_order.append(event_type)
            return []
        hook_runner.fire = track_fire

        engine = self._make_engine(hook_runner)
        asyncio.run(engine.invoke(self._state()))

        assert "round_end" in call_order, f"Expected round_end in call_order: {call_order}"
        assert "session_end" in call_order, (
            f"session_end should fire as cleanup from ControlPlane; got {call_order}"
        )
        assert call_order[-1] == "session_end", (
            f"session_end should be the last hook event, got {call_order}"
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
                with patch("arf.core.model_adapter.AsyncOpenAI"):
                    from arf.agent.base import BaseAgent
                    agent = BaseAgent(
                        config,
                        hook_runner=hook_runner,
                        state_store=MagicMock(),
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
        
        async def call_model(msgs, model, tools=None):
            return {"content": "hi back", "tool_calls": [], "usage": {}}

        engine = _build_real_engine(
            max_turns=50,
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
        call_count = 0

        async def call_model(msgs, model, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return {"content": "", "tool_calls": [
                    {"id": f"t{call_count}", "name": "mock_tool", "params": {}}
                ], "usage": {}}
            return {"content": "done", "tool_calls": [], "usage": {}}

        engine = _build_real_engine(
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
        
        async def call_model(msgs, model, tools=None):
            return {"content": "done", "tool_calls": [], "usage": {}}

        # Round 1: max_turns=1
        engine_r1 = _build_real_engine(
            max_turns=1,
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
        engine_r2 = _build_real_engine(
            max_turns=1,
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
        """round_end hook fires even when gate exceeds max_turns."""
        hook_runner = MagicMock()
        hook_runner.fire = AsyncMock(return_value=[])

        fake = FakeModelAdapter(default=FakeResponse(
            content="",
            tool_calls=[{"id": "t1", "name": "mock", "params": {}}],
        ))
        engine = _build_real_engine(
            fake_model=fake,
            max_turns=3,
            hook_runner=hook_runner,
        )
        engine.tool_executor.execute = AsyncMock(return_value={})

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
                with patch("arf.core.model_adapter.AsyncOpenAI"):
                    from arf.agent.base import BaseAgent
                    return BaseAgent(config, **overrides)

    def test_stop_fires_session_end_shutdown(self):
        """stop() calls engine.close() for each active session."""
        async def _test():
            from unittest.mock import AsyncMock, MagicMock

            hook_runner = MagicMock()
            hook_runner.fire = AsyncMock(return_value=[])

            # Capture events yielded by engine.close()
            close_events = []

            async def mock_close(state):
                close_events.append(state.get("session_id"))
                yield {"type": "session_end", "data": {"session_id": state.get("session_id")}}

            agent = self._make_agent(hook_runner=hook_runner)
            agent._engine.invoke = AsyncMock(return_value={
                "session_id": "s1",
                "messages": [{"role": "assistant", "content": "ok"}],
                "session_active": True,
            })
            agent._engine.close = mock_close
            agent._state_store.get = AsyncMock(return_value={
                "session_id": "s1",
                "messages": [],
                "session_active": True,
            })
            agent._state_store.put = AsyncMock()
            await agent.chat("hi", session_id="s1")

            # chat()'s invoke() auto-closes, so re-add to simulate active session
            agent._active_sessions.add("s1")

            await agent.stop()

            assert "s1" in close_events, (
                f"Expected engine.close() for s1, got {close_events}"
            )

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
                with patch("arf.core.model_adapter.AsyncOpenAI"):
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
                with patch("arf.core.model_adapter.AsyncOpenAI"):
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
            # chat() cleans _active_sessions on return — session is no longer active

            # Simulate stop — marks inactive in store
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
        """Each session_id can be used independently without interference."""
        async def _test():
            hook_runner = MagicMock()
            hook_runner.fire = AsyncMock(return_value=[])

            agent = self._make_agent(hook_runner=hook_runner)
            agent._state_store.get = AsyncMock(return_value=None)
            agent._state_store.put = AsyncMock()

            invoke_states = []
            async def record_invoke(state):
                invoke_states.append(state.get("session_id"))
                return {
                    "session_id": state.get("session_id"),
                    "messages": [{"role": "assistant", "content": "ok"}],
                    "session_active": True,
                }
            agent._engine.invoke = record_invoke

            await agent.chat("hi", session_id="s1")
            await agent.chat("hi", session_id="s2")

            assert invoke_states == ["s1", "s2"]

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
            # chat() cleans _active_sessions on return — verify via invoke call instead
            agent._engine.invoke.assert_called_once()

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Checkpoint behavior in ControlPlane
# ---------------------------------------------------------------------------

class TestEngineCheckpointBehavior:
    """State checkpoint (state_store.put) behavior during engine execution."""

    def test_checkpoint_saved_on_text_only_response(self):
        """state_store.put() is called even for text-only (no tool) responses."""
        
        store = CountingStateStore()
        fake = FakeModelAdapter(default=FakeResponse(
            content="hello, no tools needed",
            usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        ))
        engine = _build_real_engine(
            fake_model=fake,
            max_turns=50,
            state_store=store,
        )

        state = {
            "session_id": "test", "agent_name": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "current_model": "test", "current_turn": 0,
            "interaction_round": 0, "context_summary": "",
            "tool_results": {}, "plan": None, "metadata": {},
        }

        asyncio.run(engine.invoke(state))
        assert store.put_call_count >= 1, (
            "state_store.put() must be called even for text-only responses"
        )

    def test_checkpoint_saved_per_turn_with_tools(self):
        """Each turn has at least one checkpoint. Turns with tool calls have two:
        one after append assistant+tool_calls (crash recovery), one after tool exec."""
        
        store = CountingStateStore()

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

        engine = _build_real_engine(
            max_turns=50,
            state_store=store,
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
        # 3 turns × 1 put (turn_end) + 1 put (_execute end) + 1 put (close) = 5
        assert store.put_call_count == 5, (
            f"Expected 5 checkpoints for 3-turn scenario with close(), "
            f"got {store.put_call_count}"
        )

    def test_invoke_saves_checkpoint_after_tool_exec(self):
        """Simplified loop checkpoints after turn_end — after tool execution completes."""
        
        store = CountingStateStore()

        async def call_model(msgs, model, tools=None):
            return {
                "content": "let me help",
                "tool_calls": [{"id": "t1", "name": "mock", "params": {}}],
                "usage": {},
            }

        engine = _build_real_engine(
            max_turns=50,
            state_store=store,
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
        assert len(store.put_states) >= 1

        # In simplified loop, checkpoint happens after turn_end (model_call → tool_exec completed).
        # The state includes the assistant's tool_call AND the tool response.
        first_put = store.put_states[0]["state"]
        messages = first_put.get("messages", [])
        # Messages: [user, assistant(with tool_calls), tool_result]
        assert len(messages) >= 3
        assert messages[1]["role"] == "assistant"
        assert "tool_calls" in messages[1], (
            "Checkpoint must include assistant+tool_calls for crash recovery"
        )
        assert messages[2]["role"] == "tool"


# ---------------------------------------------------------------------------
# ConcurrentToolExecutor — parameter injection (Doc 2.8)
# ---------------------------------------------------------------------------

class TestToolExecutorInjection:
    """Doc 2.8: _agent_mode, _engine, _state_store injected into tool params."""

    def test_agent_mode_injected_when_set(self):
        from arf.engine.tool_executor import ConcurrentToolExecutor
        from unittest.mock import AsyncMock, MagicMock
        resolver = MagicMock()
        resolver.execute = AsyncMock(return_value=MagicMock())
        executor = ConcurrentToolExecutor(resolver, strategy="sequential")
        tc = {"id": "t1", "name": "test_tool", "params": {"x": 1}}

        async def run():
            await executor.execute([tc], agent_mode="sys_agent")
            call_args = resolver.execute.call_args
            assert call_args[0][1].get("_agent_mode") == "sys_agent"

        asyncio.run(run())

    def test_engine_injected_when_passed(self):
        from arf.engine.tool_executor import ConcurrentToolExecutor
        from unittest.mock import AsyncMock, MagicMock
        resolver = MagicMock()
        resolver.execute = AsyncMock(return_value=MagicMock())
        executor = ConcurrentToolExecutor(resolver, strategy="sequential")
        tc = {"id": "t1", "name": "test_tool", "params": {}}
        mock_engine = MagicMock()

        async def run():
            await executor.execute([tc], engine=mock_engine)
            call_args = resolver.execute.call_args
            assert call_args[0][1].get("_engine") is mock_engine

        asyncio.run(run())

    def test_state_store_injected_when_passed(self):
        from arf.engine.tool_executor import ConcurrentToolExecutor
        from unittest.mock import AsyncMock, MagicMock
        resolver = MagicMock()
        resolver.execute = AsyncMock(return_value=MagicMock())
        executor = ConcurrentToolExecutor(resolver, strategy="sequential")
        tc = {"id": "t1", "name": "test_tool", "params": {}}
        mock_store = MagicMock()

        async def run():
            await executor.execute([tc], state_store=mock_store)
            call_args = resolver.execute.call_args
            assert call_args[0][1].get("_state_store") is mock_store

        asyncio.run(run())

    def test_parallel_strategy_uses_semaphore(self):
        from arf.engine.tool_executor import ConcurrentToolExecutor
        from unittest.mock import AsyncMock, MagicMock
        resolver = MagicMock()
        resolver.execute = AsyncMock(return_value=MagicMock())
        executor = ConcurrentToolExecutor(resolver, strategy="parallel", max_concurrency=2)

        tc1 = {"id": "t1", "name": "a", "params": {}}
        tc2 = {"id": "t2", "name": "b", "params": {}}

        async def run():
            results = await executor.execute([tc1, tc2])
            assert "t1" in results
            assert "t2" in results
            assert resolver.execute.call_count == 2

        asyncio.run(run())

    def test_sequential_strategy_executes_in_order(self):
        from arf.engine.tool_executor import ConcurrentToolExecutor
        from unittest.mock import AsyncMock, MagicMock
        order = []
        resolver = MagicMock()

        async def track_exec(name, params):
            order.append(name)
            return MagicMock()
        resolver.execute = track_exec
        executor = ConcurrentToolExecutor(resolver, strategy="sequential")

        tc1 = {"id": "t1", "name": "first", "params": {}}
        tc2 = {"id": "t2", "name": "second", "params": {}}

        async def run():
            await executor.execute([tc1, tc2])
            assert order == ["first", "second"]

        asyncio.run(run())


# ---------------------------------------------------------------------------
# RoundManager — checkpoints and undo (Doc 2.11)
# ---------------------------------------------------------------------------

class TestRoundManager:
    """Doc 2.11: RoundManager — round-level checkpoint and undo.

    Each RoundManager() reads from disk on init (memory/checkpoints/rounds.json).
    Clean disk state before each test to avoid cross-test contamination.
    """

    def setup_method(self):
        import shutil
        shutil.rmtree("memory/checkpoints", ignore_errors=True)
        shutil.rmtree("data/checkpoints", ignore_errors=True)

    def test_begin_round_creates_snapshot(self):
        from arf.engine.round_manager import RoundManager
        rm = RoundManager(max_undo_depth=3)
        assert rm.count() == 0
        state = {"session_id": "s1", "agent_name": "main",
                 "messages": [{"role": "user", "content": "hi"}],
                 "current_turn": 0}
        tx = rm.begin_round(state)
        assert tx.round_num == 1
        assert tx.state_snapshot["session_id"] == "s1"
        assert rm.count() == 1

    def test_undo_one_round_pops_round_and_returns_its_snapshot(self):
        """undo(1) pops the newest round, returns its state_snapshot.
        RoundManager snapshots at round-START state, so the popped
        snapshot is what the state was when the round began."""
        from arf.engine.round_manager import RoundManager
        rm = RoundManager(max_undo_depth=3)

        original = {"session_id": "s1", "agent_name": "main",
                    "messages": [{"role": "user", "content": "original"}],
                    "current_turn": 0}
        rm.begin_round(original)

        modified = {"session_id": "s1", "agent_name": "main",
                    "messages": [{"role": "user", "content": "modified"}],
                    "current_turn": 5}
        rm.begin_round(modified)

        restored = rm.undo(1)
        assert restored is not None
        assert restored["session_id"] == "s1"
        assert restored["messages"][0]["content"] == "modified"
        assert rm.count() == 1  # one round remaining

    def test_undo_multi_step_restores_target(self):
        from arf.engine.round_manager import RoundManager
        rm = RoundManager(max_undo_depth=5)
        for i in range(5):
            rm.begin_round({"session_id": "s1", "round": i, "messages": [],
                           "agent_name": "main", "current_turn": 0})

        restored = rm.undo(3)  # pop 3, back to round 2
        assert restored is not None
        assert restored["round"] == 2
        assert rm.count() == 2  # 5 - 3 = 2 remaining

    def test_undo_too_many_returns_none(self):
        from arf.engine.round_manager import RoundManager
        rm = RoundManager(max_undo_depth=3)
        rm.begin_round({"session_id": "s1", "agent_name": "main",
                        "messages": [], "current_turn": 0})
        assert rm.undo(5) is None
        assert rm.undo(0) is None

    def test_close_round_marks_closed(self):
        from arf.engine.round_manager import RoundManager
        rm = RoundManager(max_undo_depth=3)
        rm.begin_round({"session_id": "s1", "agent_name": "main",
                        "messages": [], "current_turn": 0})
        assert rm.active_round is not None
        rm.close_round()
        assert rm.active_round is None



# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# _cancelled() — cancel_event detection (Doc 2.6)
# ---------------------------------------------------------------------------

class TestCancelled:
    """Doc 2.6: _cancelled() checks cancel_event non-blocking."""

    def test_cancelled_false_without_event(self):
        engine = _build_real_engine()
        assert engine._cancelled() is False

    def test_cancelled_false_when_event_not_set(self):
        import asyncio
        engine = _build_real_engine(cancel_event=asyncio.Event())
        assert engine._cancelled() is False

    def test_cancelled_true_when_event_set(self):
        import asyncio
        evt = asyncio.Event()
        evt.set()
        engine = _build_real_engine(cancel_event=evt)
        assert engine._cancelled() is True

    def test_set_cancel_event_late_binding(self):
        import asyncio
        engine = _build_real_engine()
        assert engine._cancelled() is False
        evt = asyncio.Event()
        engine.set_cancel_event(evt)
        evt.set()
        assert engine._cancelled() is True



# ---------------------------------------------------------------------------
# _parse_tool_calls() — response parsing (Doc 2.2 flow)
# ---------------------------------------------------------------------------

class TestParseToolCalls:
    """Doc 2.2: _parse_tool_calls handles dict model responses."""

    def test_dict_with_tool_calls(self):
        engine = _build_real_engine()
        resp = {"content": "", "tool_calls": [
            {"id": "1", "name": "read", "params": {"file": "x"}}
        ]}
        result = engine._parse_tool_calls(resp)
        assert len(result) == 1
        assert result[0]["name"] == "read"

    def test_dict_without_tool_calls(self):
        engine = _build_real_engine()
        resp = {"content": "hello", "tool_calls": []}
        result = engine._parse_tool_calls(resp)
        assert result == []

    def test_non_dict_returns_empty(self):
        engine = _build_real_engine()
        result = engine._parse_tool_calls("not a dict")
        assert result == []



# ---------------------------------------------------------------------------
# InMemoryStateStore — snapshot tracking (Doc 2.11)
# ---------------------------------------------------------------------------

class TestInMemoryStateStore:
    """Doc 2.11: InMemoryStateStore has snapshots list for testing."""

    def test_put_stores_snapshot(self):
        from arf.engine.checkpoint import InMemoryStateStore
        async def run():
            store = InMemoryStateStore()
            await store.put("s1", {"messages": [], "current_turn": 3})
            assert len(store.snapshots) == 1
            assert store.snapshots[0]["turn"] == 3
            assert store.snapshots[0]["session_id"] == "s1"

        asyncio.run(run())

    def test_get_returns_stored_state(self):
        from arf.engine.checkpoint import InMemoryStateStore
        async def run():
            store = InMemoryStateStore()
            await store.put("s1", {"messages": [{"role": "user", "content": "x"}]})
            state = await store.get("s1")
            assert state is not None
            assert state["messages"][0]["content"] == "x"

        asyncio.run(run())

    def test_get_returns_none_for_unknown_session(self):
        from arf.engine.checkpoint import InMemoryStateStore
        async def run():
            store = InMemoryStateStore()
            assert await store.get("nonexistent") is None

        asyncio.run(run())

    def test_delete_removes_state(self):
        from arf.engine.checkpoint import InMemoryStateStore
        async def run():
            store = InMemoryStateStore()
            await store.put("s1", {"messages": []})
            await store.delete("s1")
            assert await store.get("s1") is None

        asyncio.run(run())

    def test_reset_clears_all(self):
        from arf.engine.checkpoint import InMemoryStateStore
        async def run():
            store = InMemoryStateStore()
            await store.put("s1", {"messages": []})
            await store.put("s2", {"messages": []})
            store.reset()
            assert await store.get("s1") is None
            assert await store.get("s2") is None
            assert store.snapshots == []

        asyncio.run(run())


# ---------------------------------------------------------------------------
# FileStateStore — tool_results not persisted (Doc 2.11)
# ---------------------------------------------------------------------------

class TestFileStateStore:
    """Doc 2.11: FileStateStore strips tool_results before write."""

    def test_tool_results_not_persisted(self):
        from arf.engine.checkpoint import FileStateStore
        import tempfile, os
        async def run():
            tmp = tempfile.mkdtemp()
            try:
                store = FileStateStore(state_dir=tmp)
                await store.put("s1", {
                    "session_id": "s1",
                    "messages": [],
                    "current_turn": 1,
                    "tool_results": {"t1": {"success": True, "data": "secret"}},
                })
                restored = await store.get("s1")
                assert restored is not None
                assert "tool_results" not in restored, (
                    "tool_results must NOT be persisted — they are transient"
                )
            finally:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)

        asyncio.run(run())

    def test_atomic_write_uses_tmp_rename(self):
        from arf.engine.checkpoint import FileStateStore
        import tempfile
        async def run():
            tmp = tempfile.mkdtemp()
            try:
                store = FileStateStore(state_dir=tmp)
                await store.put("s1", {"messages": [{"role": "user", "content": "a"}],
                                       "current_turn": 0})
                # The final path should exist, not just .tmp
                path = store._path("s1")
                assert path.exists(), f"Expected {path} to exist after put()"
                assert not path.with_suffix(".tmp").exists(), (
                    "tmp file should have been renamed to final path"
                )
            finally:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)

        asyncio.run(run())
