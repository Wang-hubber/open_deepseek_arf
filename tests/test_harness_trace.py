"""Tests for harness trace writer — JSONL output from ctx.emit()."""
import json
import uuid
from pathlib import Path

import pytest

from arf.agent.state import ModelResult
from arf.agent.primitive import PrimitiveAgent
from arf.harness.engine import AgentHarness


def make_agent(call_model):
    return PrimitiveAgent(
        agent_id="t1",
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=call_model,
    )


class FakeToolResult:
    def __init__(self, success=True, data="result_ok", error=""):
        self.success = success
        self.data = data
        self.error = error
        self.duration_ms = 10


class FakeToolExecutor:
    def __init__(self):
        self.calls: list = []

    async def get_tool_definitions(self):
        return [{"name": "echo", "description": "echo tool", "parameters": {}}]

    async def execute(self, name, params):
        self.calls.append((name, params))
        return FakeToolResult()


class TestHarnessTraceWriter:
    @pytest.mark.anyio
    async def test_trace_jsonl_written(self, tmp_path):
        """Trace file is created and contains events from a run."""
        data_dir = str(tmp_path)

        async def fake_call(messages, tools=None):
            return ModelResult(content="hi", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[], tool_manager=None, data_dir=data_dir)
        events = [e async for e in harness.run("hello")]

        session_id = agent.state.session_id
        trace_file = Path(data_dir) / session_id / "traces" / f"{session_id}.jsonl"
        assert trace_file.exists()

        lines = trace_file.read_text().strip().split("\n")
        records = [json.loads(line) for line in lines]
        event_types = [r["type"] for r in records]
        assert "session_start" in event_types
        assert "user_input" in event_types
        assert "model_call_end" in event_types
        assert "round_end" in event_types

        # session_start has meaningful data
        ss = next(r for r in records if r["type"] == "session_start")
        assert ss["data"]["session_id"] == session_id
        assert ss["data"]["is_new"] is True

        # user_input has the message content
        ui = next(r for r in records if r["type"] == "user_input")
        assert ui["data"]["content"] == "hello"

        # round_end has summary data
        re = next(r for r in records if r["type"] == "round_end")
        assert re["data"]["round"] >= 1
        assert re["data"]["turns"] >= 1
        assert re["data"]["stopped"] in ("completed", "max_turns")

    @pytest.mark.anyio
    async def test_chunk_events_filtered(self, tmp_path):
        """model_chunk and thinking_delta are NOT written to JSONL — verified via direct trace writer dispatch.

        This test directly exercises the trace writer's CHUNK_EVENTS filtering by
        emitting events through the trace queue without running the full harness loop.
        It will FAIL if CHUNK_EVENTS filtering is broken.
        """
        data_dir = str(tmp_path)
        session_id = str(uuid.uuid4())

        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        agent.state.session_id = session_id
        harness = AgentHarness(agent, plugins=[], tool_manager=None, data_dir=data_dir)

        # Start trace writer directly (don't run the full harness loop)
        harness._start_trace_writer(session_id)

        # Get context to emit events
        ctx = harness._make_ctx()

        # Emit chunk events — should be filtered out by _trace_writer
        ctx.emit(event_type="model_chunk", data={"content": "hello"})
        ctx.emit(event_type="thinking_delta", data={"delta": "thinking..."})
        ctx.emit(event_type="thinking_delta", data={"delta": "more thinking"})

        # Emit a non-chunk event — should appear in trace
        ctx.emit(event_type="tool_call_start", data={"tool": "echo"})

        # Stop writer: drains queue (processing all events), cancels task, closes file
        await harness._stop_trace_writer()

        # Read the trace file
        trace_file = Path(data_dir) / session_id / "traces" / f"{session_id}.jsonl"
        assert trace_file.exists()
        lines = trace_file.read_text().splitlines()

        # Only the tool_call_start event should be present
        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {lines}"
        evt = json.loads(lines[0])
        assert evt["type"] == "tool_call_start"

    @pytest.mark.anyio
    async def test_tool_events_in_trace(self, tmp_path):
        """tool_call_start and tool_call_end appear in trace JSONL."""
        data_dir = str(tmp_path)
        turn = 0

        async def fake_call(messages, tools=None):
            nonlocal turn
            turn += 1
            if turn == 1:
                return ModelResult(
                    content="",
                    tool_calls=[{"id": "t1", "name": "echo", "params": {"msg": "hi"}}],
                    usage={}, finish_reason="tool_calls",
                )
            return ModelResult(content="done", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        tool_exec = FakeToolExecutor()
        harness = AgentHarness(agent, plugins=[], tool_manager=tool_exec, data_dir=data_dir)
        events = [e async for e in harness.run("echo hi")]

        session_id = agent.state.session_id
        trace_file = Path(data_dir) / session_id / "traces" / f"{session_id}.jsonl"
        lines = trace_file.read_text().strip().split("\n")
        event_types = [json.loads(line)["type"] for line in lines]
        assert "tool_call_start" in event_types
        assert "tool_call_end" in event_types

    @pytest.mark.anyio
    async def test_trace_agent_event_schema(self, tmp_path):
        """Each JSONL line has AgentEvent fields."""
        data_dir = str(tmp_path)

        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[], tool_manager=None, data_dir=data_dir)
        events = [e async for e in harness.run("hi")]

        session_id = agent.state.session_id
        trace_file = Path(data_dir) / session_id / "traces" / f"{session_id}.jsonl"
        lines = trace_file.read_text().strip().split("\n")
        record = json.loads(lines[0])
        assert "type" in record
        assert "data" in record
        assert "timestamp" in record
        assert "session_id" in record
        assert record["session_id"] == session_id
