"""Unit tests for EvalRunner with real trace capture."""
import pytest

from arf.core.events import AgentEvent
from arf.event_bus import InMemoryEventBus
from arf.evaluation.models import EvalCase, EvalBenchmark
from arf.evaluation.runner import EvalRunner


class FakeAgent:
    """Minimal agent stub that emits events and returns a response."""
    def __init__(self, bus):
        self.event_bus = bus
        self.config = "fake_config"

    async def chat(self, user_message: str, session_id: str = "default") -> str:
        self.event_bus.emit(AgentEvent(
            type="user_input", turn=1, session_id=session_id,
            data={"content": user_message},
        ))
        self.event_bus.emit(AgentEvent(
            type="tool_call_start", turn=1, session_id=session_id,
            data={"tool_name": "file_writer"},
        ))
        self.event_bus.emit(AgentEvent(
            type="tool_call_end", turn=1, session_id=session_id,
            data={"tool_name": "file_writer", "success": True,
                  "duration_ms": 10, "result": '{"ok":true}', "error": ""},
        ))
        self.event_bus.emit(AgentEvent(
            type="model_call_end", turn=1, session_id=session_id,
            data={"model": "test", "content": "File created: x.py",
                  "usage": {"total_tokens": 50}},
        ))
        return "File created: x.py"


class TestEvalRunner:
    @pytest.fixture
    def bus(self):
        return InMemoryEventBus()

    @pytest.fixture
    def agent(self, bus):
        return FakeAgent(bus)

    @pytest.fixture
    def benchmark(self):
        return EvalBenchmark(
            name="test_bm",
            cases=[
                EvalCase(id="c0", input="create x.py",
                         expected_tools=["file_writer"]),
            ],
        )

    @pytest.mark.anyio
    async def test_run_captures_trace(self, agent, bus, benchmark):
        runner = EvalRunner(agent, bus)
        report = await runner.run(benchmark)

        assert report.benchmark_name == "test_bm"
        assert report.summary.total == 1
        assert report.summary.passed == 1
        assert len(report.per_case) == 1
        case = report.per_case[0]
        assert case["passed"] is True
        trace = case["trace"]
        assert len(trace["turns"]) >= 1

    @pytest.mark.anyio
    async def test_run_metrics_computed(self, agent, bus, benchmark):
        runner = EvalRunner(agent, bus)
        report = await runner.run(benchmark)
        case = report.per_case[0]
        assert "metrics" in case
        # SuccessRateMetric: trace has no errors -> 1.0
        assert case["metrics"].get("success_rate") == 1.0

    @pytest.mark.anyio
    async def test_run_case_failure_captured(self, bus, benchmark):
        class FailingAgent:
            event_bus = bus
            config = "fake_config"
            async def chat(self, user_message="", session_id="default"):
                raise RuntimeError("model down")

        runner = EvalRunner(FailingAgent(), bus)
        report = await runner.run(benchmark)
        assert report.summary.failed == 1
        assert report.per_case[0]["passed"] is False
        assert report.per_case[0]["error"] == "model down"
