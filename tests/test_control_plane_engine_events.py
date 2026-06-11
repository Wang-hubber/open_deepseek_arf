"""Tests for engine event injection in ControlPlane."""
import asyncio
import pytest
from arf.engine.control_plane import ControlPlane
from arf.core.plugin_context import PluginContext


class FakeStateStore:
    def __init__(self):
        self.data = {}
    async def put(self, sid, state):
        self.data[sid] = dict(state)
    async def get(self, sid):
        return self.data.get(sid)


class FakeToolExecutor:
    def __init__(self, results=None):
        self._results = results or {}
    async def execute(self, tool_calls, **kwargs):
        results = {}
        for tc in tool_calls:
            tc_id = tc.get("id", "")
            info = self._results.get(tc_id, {})
            results[tc_id] = type("TR", (), {
                "success": info.get("success", True),
                "data": info.get("data", "result"),
                "error": info.get("error"),
                "duration_ms": info.get("duration_ms", 42),
            })()
        return results


class TestEngineEventInjection:
    def test_model_call_injects_event(self):
        """After model_call, ctx.hook_data._engine_events should have model_call entry."""
        store = FakeStateStore()
        tools = FakeToolExecutor()

        async def fake_call_model(msgs, model, tools=None):
            return {"content": "hello", "tool_calls": [],
                    "usage": {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}}

        engine = ControlPlane(
            state_store=store,
            tool_executor=tools,
            call_model=fake_call_model,
        )

        state = {
            "session_id": "t1",
            "messages": [{"role": "user", "content": "hi"}],
            "current_turn": 1,
            "current_model": "deepseek-v3",
        }
        ctx = engine._make_ctx(state, "t1", 1, "pre_action")

        async def _run():
            async for _ in engine._action_call_model(state, ctx):
                pass
            return ctx

        ctx = asyncio.run(_run())
        events = ctx.hook_data.get("_engine_events", [])
        assert len(events) >= 1, f"Expected >= 1 engine event, got {len(events)}"
        model_events = [e for e in events if e["type"] == "model_call"]
        assert len(model_events) == 1
        assert model_events[0]["data"]["model"] == "deepseek-v3"
        assert model_events[0]["data"]["input_tokens"] == 5
        assert model_events[0]["data"]["output_tokens"] == 5
        assert model_events[0]["data"]["content"] == "hello"
        assert "timestamp" in model_events[0]

    def test_tool_exec_injects_event(self):
        """After tool_exec, ctx.hook_data._engine_events should have tool_call entries."""
        store = FakeStateStore()
        tools = FakeToolExecutor({
            "tc1": {"success": True, "data": "file content", "duration_ms": 42},
        })

        engine = ControlPlane(
            state_store=store,
            tool_executor=tools,
        )

        state = {
            "session_id": "t1",
            "messages": [{"role": "user", "content": "hi"}],
            "current_turn": 1,
            "_pending_tool_calls": [
                {"id": "tc1", "name": "read", "params": {"path": "x"}}
            ],
        }
        ctx = engine._make_ctx(state, "t1", 1, "pre_action")

        async def _run():
            async for _ in engine._action_execute_tools(state, ctx):
                pass
            return ctx

        ctx = asyncio.run(_run())
        events = ctx.hook_data.get("_engine_events", [])
        assert len(events) >= 1, f"Expected >= 1 engine event, got {len(events)}"
        tool_events = [e for e in events if e["type"] == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0]["data"]["tool_name"] == "read"
        assert tool_events[0]["data"]["success"] is True
        assert tool_events[0]["data"]["duration_ms"] == 42
        assert "path" in tool_events[0]["data"]["params"]
