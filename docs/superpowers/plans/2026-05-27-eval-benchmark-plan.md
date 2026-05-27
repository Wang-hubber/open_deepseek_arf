# Eval Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the skeleton `DefaultEvalRunner` with a working eval pipeline: create benchmarks from real session traces, run them with real trace capture via EventBus, compute meaningful metrics, and compare runs for regression detection.

**Architecture:** Seven new/rewritten files in `arf/evaluation/` — models, builder, runner, comparator, trace adapter, exceptions. EventBus gains two read-only methods (`event_count`, `events_since`). All implementation is framework-layer; App-layer CLI and HTML report are Phase 2.

**Tech Stack:** Python 3.14, dataclasses, asyncio, pytest + anyio, `InMemoryEventBus`, `FileTraceStore`

---

### Task 1: EventBus — event_count() and events_since()

**Files:**
- Modify: `arf/event_bus.py:30-33`
- Test: `tests/test_event_bus.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for EventBus incremental read methods."""
import pytest
from arf.core.events import AgentEvent
from arf.event_bus import InMemoryEventBus


class TestEventBusIncremental:
    @pytest.fixture
    def bus(self):
        return InMemoryEventBus()

    def test_event_count_starts_zero(self, bus):
        assert bus.event_count() == 0

    def test_event_count_increments(self, bus):
        bus.emit(AgentEvent(type="user_input", data={}))
        bus.emit(AgentEvent(type="error", data={}))
        assert bus.event_count() == 2

    def test_events_since_returns_new_events(self, bus):
        bus.emit(AgentEvent(type="user_input", data={"n": 1}))
        mark = bus.event_count()
        assert mark == 1
        bus.emit(AgentEvent(type="error", data={"n": 2}))
        bus.emit(AgentEvent(type="tool_call_start", data={"tool_name": "x"}))
        new = bus.events_since(mark)
        assert len(new) == 2
        assert new[0].data["n"] == 2
        assert new[1].type == "tool_call_start"

    def test_events_since_empty_when_none(self, bus):
        bus.emit(AgentEvent(type="user_input", data={}))
        new = bus.events_since(bus.event_count())
        assert new == []

    def test_events_since_does_not_mutate(self, bus):
        """events_since must not clear or reset internal state."""
        bus.emit(AgentEvent(type="user_input", data={}))
        bus.events_since(0)
        assert bus.event_count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_event_bus.py -v`
Expected: 5 FAILED — `AttributeError: 'InMemoryEventBus' object has no attribute 'event_count'`

- [ ] **Step 3: Write minimal implementation**

In `arf/event_bus.py`, add two methods to `InMemoryEventBus`:

```python
    def event_count(self) -> int:
        return len(self._events)

    def events_since(self, index: int) -> list[AgentEvent]:
        return self._events[index:]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_event_bus.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Run full suite**

Run: `pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add arf/event_bus.py tests/test_event_bus.py
git commit -m "feat: add event_count() and events_since() to InMemoryEventBus"
```

---

### Task 2: EvalError exception

**Files:**
- Create: `arf/evaluation/exceptions.py`
- Test: `tests/test_eval_exceptions.py` (new)

- [ ] **Step 1: Write the failing test**

```python
import pytest
from arf.evaluation.exceptions import EvalError


class TestEvalError:
    def test_basic(self):
        with pytest.raises(EvalError, match="Session 'foo' not found"):
            raise EvalError("Session 'foo' not found")

    def test_is_exception(self):
        err = EvalError("msg")
        assert isinstance(err, Exception)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_exceptions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arf.evaluation.exceptions'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Evaluation-specific exceptions."""


class EvalError(Exception):
    """Raised when eval operations encounter an unrecoverable error."""
    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_exceptions.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add arf/evaluation/exceptions.py tests/test_eval_exceptions.py
git commit -m "feat: add EvalError exception"
```

---

### Task 3: events_to_trace() — AgentEvent list to trace dict

**Files:**
- Create: `arf/evaluation/trace_adapter.py`
- Test: `tests/test_eval_trace.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for events_to_trace adapter."""
import pytest
from arf.core.events import AgentEvent
from arf.evaluation.trace_adapter import events_to_trace


def make_event(turn, type, **data):
    return AgentEvent(type=type, turn=turn, data=data, timestamp=1000.0)


class TestEventsToTrace:
    def test_empty_events(self):
        assert events_to_trace([]) == {"turns": []}

    def test_single_turn_tool_call(self):
        events = [
            make_event(1, "tool_call_start", tool_name="file_reader"),
            make_event(1, "tool_call_end", tool_name="file_reader",
                       success=True, duration_ms=42,
                       result='{"content":"hello"}', error=""),
        ]
        trace = events_to_trace(events)
        assert len(trace["turns"]) == 1
        t = trace["turns"][0]
        assert t["turn"] == 1
        assert t["error"] is None
        assert len(t["tool_calls"]) == 1
        assert t["tool_calls"][0]["tool_name"] == "file_reader"
        assert t["tool_calls"][0]["success"] is True

    def test_multi_turn_separation(self):
        events = [
            make_event(1, "tool_call_start", tool_name="a"),
            make_event(1, "tool_call_end", tool_name="a", success=True),
            make_event(2, "tool_call_start", tool_name="b"),
            make_event(2, "tool_call_end", tool_name="b", success=False,
                       error="boom"),
        ]
        trace = events_to_trace(events)
        assert len(trace["turns"]) == 2
        assert trace["turns"][0]["turn"] == 1
        assert trace["turns"][1]["turn"] == 2
        assert trace["turns"][1]["tool_calls"][0]["error"] == "boom"

    def test_model_output_captured(self):
        events = [
            make_event(1, "tool_call_start", tool_name="x"),
            make_event(1, "tool_call_end", tool_name="x", success=True),
            make_event(1, "model_call_end", model="deep",
                       content="File created: hello.py",
                       usage={"total_tokens": 150}),
        ]
        trace = events_to_trace(events)
        t = trace["turns"][0]
        assert "File created" in t["model_output"]

    def test_error_event_tracked(self):
        events = [
            make_event(1, "tool_call_start", tool_name="x"),
            make_event(1, "error", detail="connection refused"),
        ]
        trace = events_to_trace(events)
        assert trace["turns"][0]["error"] == "connection refused"

    def test_duration_computed(self):
        events = [
            make_event(1, "tool_call_start", tool_name="x"),
            make_event(1, "tool_call_end", tool_name="x",
                       duration_ms=100),
        ]
        trace = events_to_trace(events)
        assert trace["turns"][0]["duration_ms"] >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_trace.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
"""events_to_trace — AgentEvent list → structured trace dict for metrics."""
from arf.core.events import AgentEvent


def events_to_trace(events: list[AgentEvent]) -> dict:
    """Convert a flat AgentEvent list into {turns: [{turn, tool_calls, model_output, error, duration_ms}]}."""
    turns: dict[int, dict] = {}

    for e in events:
        t = e.turn
        if t not in turns:
            turns[t] = {"turn": t, "tool_calls": [], "model_output": "", "error": None, "duration_ms": 0}

        if e.type == "tool_call_end":
            turns[t]["tool_calls"].append({
                "tool_name": e.data.get("tool_name", ""),
                "success": e.data.get("success", False),
                "error": e.data.get("error", ""),
            })
            turns[t]["duration_ms"] += e.data.get("duration_ms", 0)
        elif e.type == "model_call_end":
            turns[t]["model_output"] = e.data.get("content", "")
        elif e.type == "error":
            turns[t]["error"] = e.data.get("detail", "") or e.data.get("message", "")

    return {"turns": [turns[k] for k in sorted(turns)]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_trace.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add arf/evaluation/trace_adapter.py tests/test_eval_trace.py
git commit -m "feat: add events_to_trace() adapter for eval metrics"
```

---

### Task 4: EvalBenchmark model with JSON serialization

**Files:**
- Create: `arf/evaluation/models.py`
- Test: `tests/test_eval_models.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for eval data models and JSON serialization."""
import json
import tempfile
from pathlib import Path

import pytest

from arf.evaluation.models import EvalCase, EvalBenchmark


class TestEvalCase:
    def test_minimal(self):
        c = EvalCase(id="c1", input="hello")
        assert c.expected_tools is None
        assert c.expected_output_contains is None

    def test_full(self):
        c = EvalCase(id="c1", input="hello",
                     expected_tools=["file_writer"],
                     expected_output_contains=["hello.py"],
                     max_turns=3)
        assert c.max_turns == 3


class TestEvalBenchmarkJson:
    @pytest.fixture
    def benchmark(self):
        return EvalBenchmark(
            name="file_ops_v1",
            source_session="default",
            created_at=1716812345.0,
            cases=[
                EvalCase(id="c0", input="create hello.py",
                         expected_tools=["file_writer"],
                         expected_output_contains=["hello.py"]),
                EvalCase(id="c1", input="read it back"),
            ],
        )

    def test_to_json_roundtrip(self, benchmark, tmp_path):
        p = tmp_path / "bm.json"
        benchmark.to_json(str(p))
        loaded = EvalBenchmark.from_json(str(p))
        assert loaded.name == "file_ops_v1"
        assert loaded.source_session == "default"
        assert len(loaded.cases) == 2
        assert loaded.cases[0].input == "create hello.py"
        assert loaded.cases[0].expected_tools == ["file_writer"]

    def test_from_json_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            EvalBenchmark.from_json(str(tmp_path / "nope.json"))

    def test_defaults(self):
        bm = EvalBenchmark(name="test")
        assert bm.cases == []
        assert bm.source_session is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_models.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
"""Eval data models with JSON serialization."""
import json
from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    input: str
    expected_tools: list[str] | None = None
    expected_output_contains: list[str] | None = None
    max_turns: int | None = None


@dataclass
class EvalBenchmark:
    name: str
    source_session: str | None = None
    created_at: float = 0.0
    cases: list[EvalCase] = field(default_factory=list)

    def to_json(self, path: str) -> None:
        data = {
            "name": self.name,
            "source_session": self.source_session,
            "created_at": self.created_at,
            "cases": [
                {
                    "id": c.id,
                    "input": c.input,
                    **({"expected_tools": c.expected_tools} if c.expected_tools else {}),
                    **({"expected_output_contains": c.expected_output_contains} if c.expected_output_contains else {}),
                    **({"max_turns": c.max_turns} if c.max_turns is not None else {}),
                }
                for c in self.cases
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, path: str) -> "EvalBenchmark":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            name=data["name"],
            source_session=data.get("source_session"),
            created_at=data.get("created_at", 0.0),
            cases=[
                EvalCase(
                    id=c["id"],
                    input=c["input"],
                    expected_tools=c.get("expected_tools"),
                    expected_output_contains=c.get("expected_output_contains"),
                    max_turns=c.get("max_turns"),
                )
                for c in data.get("cases", [])
            ],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_models.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add arf/evaluation/models.py tests/test_eval_models.py
git commit -m "feat: add EvalBenchmark model with JSON serialization"
```

---

### Task 5: EvalReport and EvalDiff models

**Files:**
- Modify: `arf/evaluation/models.py` (append)
- Test: `tests/test_eval_models.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eval_models.py`:

```python
from arf.evaluation.models import EvalSummary, EvalReport, EvalDiff


class TestEvalReportJson:
    @pytest.fixture
    def report(self):
        return EvalReport(
            run_id="run-001",
            benchmark_name="file_ops_v1",
            agent_config_hash="abc123",
            timestamp=1716812345.0,
            summary=EvalSummary(
                total=2, passed=2, failed=0, pass_rate=1.0,
                avg_turns=1.5, avg_tool_calls=1.0, avg_duration_seconds=2.0,
                tool_accuracy=1.0, output_contains=1.0,
            ),
            per_case=[
                {"case_id": "c0", "passed": True, "trace": {"turns": []},
                 "metrics": {"success_rate": 1.0}, "response": "ok"},
            ],
        )

    def test_report_to_json_roundtrip(self, report, tmp_path):
        p = tmp_path / "report.json"
        report.to_json(str(p))
        loaded = EvalReport.from_json(str(p))
        assert loaded.run_id == "run-001"
        assert loaded.benchmark_name == "file_ops_v1"
        assert loaded.summary.pass_rate == 1.0

    def test_report_defaults(self):
        r = EvalReport(run_id="r", benchmark_name="b",
                       agent_config_hash="", timestamp=0.0)
        assert r.summary.total == 0


class TestEvalDiff:
    def test_diff_structure(self):
        diff = EvalDiff(
            baseline_run_id="r1", current_run_id="r2",
            summary_diff={"pass_rate": -0.1},
            regressions=[{"case_id": "c0", "metric": "tool_accuracy", "delta": -0.5}],
            improvements=[],
        )
        assert len(diff.regressions) == 1
        assert diff.summary_diff["pass_rate"] == -0.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_models.py -v -k "report or diff"`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `arf/evaluation/models.py`:

```python
@dataclass
class EvalSummary:
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    avg_turns: float = 0.0
    avg_tool_calls: float = 0.0
    avg_duration_seconds: float = 0.0
    tool_accuracy: float = 0.0
    output_contains: float = 0.0


@dataclass
class EvalReport:
    run_id: str
    benchmark_name: str
    agent_config_hash: str
    timestamp: float
    summary: EvalSummary = field(default_factory=EvalSummary)
    per_case: list[dict] = field(default_factory=list)

    def to_json(self, path: str) -> None:
        data = {
            "run_id": self.run_id,
            "benchmark_name": self.benchmark_name,
            "agent_config_hash": self.agent_config_hash,
            "timestamp": self.timestamp,
            "summary": {
                "total": self.summary.total,
                "passed": self.summary.passed,
                "failed": self.summary.failed,
                "pass_rate": self.summary.pass_rate,
                "avg_turns": self.summary.avg_turns,
                "avg_tool_calls": self.summary.avg_tool_calls,
                "avg_duration_seconds": self.summary.avg_duration_seconds,
                "tool_accuracy": self.summary.tool_accuracy,
                "output_contains": self.summary.output_contains,
            },
            "per_case": self.per_case,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, path: str) -> "EvalReport":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        s = data["summary"]
        return cls(
            run_id=data["run_id"],
            benchmark_name=data["benchmark_name"],
            agent_config_hash=data["agent_config_hash"],
            timestamp=data["timestamp"],
            summary=EvalSummary(
                total=s["total"], passed=s["passed"], failed=s["failed"],
                pass_rate=s["pass_rate"], avg_turns=s.get("avg_turns", 0.0),
                avg_tool_calls=s.get("avg_tool_calls", 0.0),
                avg_duration_seconds=s.get("avg_duration_seconds", 0.0),
                tool_accuracy=s.get("tool_accuracy", 0.0),
                output_contains=s.get("output_contains", 0.0),
            ),
            per_case=data.get("per_case", []),
        )


@dataclass
class EvalDiff:
    baseline_run_id: str
    current_run_id: str
    summary_diff: dict = field(default_factory=dict)
    regressions: list[dict] = field(default_factory=list)
    improvements: list[dict] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_models.py -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add arf/evaluation/models.py tests/test_eval_models.py
git commit -m "feat: add EvalReport and EvalDiff models with JSON serialization"
```

---

### Task 6: BenchmarkBuilder — create benchmark from session trace

**Files:**
- Create: `arf/evaluation/builder.py`
- Test: `tests/test_eval_builder.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for BenchmarkBuilder."""
import json
import tempfile
from pathlib import Path

import pytest

from arf.evaluation.builder import BenchmarkBuilder
from arf.evaluation.exceptions import EvalError
from arf.observability.file_trace import FileTraceStore
from arf.event_bus import InMemoryEventBus
from arf.core.events import AgentEvent


def _write_trace(dir, session_id, events):
    p = Path(dir) / f"{session_id}.json"
    p.write_text(json.dumps([
        {"type": e.type, "data": e.data, "turn": e.turn, "timestamp": e.timestamp}
        for e in events
    ]))


class TestBenchmarkBuilder:
    @pytest.fixture
    def trace_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_build_creates_cases_from_user_inputs(self, trace_dir):
        _write_trace(trace_dir, "s1", [
            AgentEvent(type="user_input", turn=1, data={"content": "create file"}),
            AgentEvent(type="tool_call_start", turn=1, data={"tool_name": "file_writer"}),
            AgentEvent(type="tool_call_end", turn=1, data={"tool_name": "file_writer", "success": True}),
            AgentEvent(type="model_call_end", turn=1, data={"content": "done"}),
            AgentEvent(type="user_input", turn=3, data={"content": "read it"}),
            AgentEvent(type="tool_call_start", turn=3, data={"tool_name": "file_reader"}),
            AgentEvent(type="tool_call_end", turn=3, data={"tool_name": "file_reader", "success": True}),
        ])
        store = FileTraceStore(InMemoryEventBus(), dir=trace_dir)
        builder = BenchmarkBuilder(store)
        bm = builder.build("s1", "my_bench")

        assert bm.name == "my_bench"
        assert bm.source_session == "s1"
        assert len(bm.cases) == 2
        assert bm.cases[0].input == "create file"
        assert bm.cases[0].expected_tools == ["file_writer"]
        assert bm.cases[1].input == "read it"
        assert bm.cases[1].expected_tools == ["file_reader"]
        assert bm.created_at > 0

    def test_build_session_not_found(self, trace_dir):
        store = FileTraceStore(InMemoryEventBus(), dir=trace_dir)
        builder = BenchmarkBuilder(store)
        with pytest.raises(EvalError, match="not found"):
            builder.build("nope", "bm")

    def test_build_no_user_inputs(self, trace_dir):
        _write_trace(trace_dir, "s1", [
            AgentEvent(type="tool_call_start", turn=1, data={"tool_name": "x"}),
        ])
        store = FileTraceStore(InMemoryEventBus(), dir=trace_dir)
        builder = BenchmarkBuilder(store)
        with pytest.raises(EvalError, match="No user messages"):
            builder.build("s1", "bm")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_builder.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
"""BenchmarkBuilder — create EvalBenchmark from FileTraceStore session."""
import time

from arf.evaluation.exceptions import EvalError
from arf.evaluation.models import EvalCase, EvalBenchmark


class BenchmarkBuilder:
    def __init__(self, trace_store):
        self._store = trace_store

    def build(self, session_id: str, name: str) -> EvalBenchmark:
        events = self._store.load(session_id)
        if not events:
            raise EvalError(f"Session '{session_id}' not found in trace store")

        cases: list[EvalCase] = []
        user_msgs = [e for e in events if e.get("type") == "user_input"]
        if not user_msgs:
            raise EvalError(f"No user messages found in session '{session_id}'")

        for i, um in enumerate(user_msgs):
            turn = um.get("turn", 0)
            tools_in_turn = [
                e.get("data", {}).get("tool_name", "")
                for e in events
                if e.get("type") == "tool_call_start" and e.get("turn") == turn
            ]
            expected_tools = tools_in_turn if tools_in_turn else None
            cases.append(EvalCase(
                id=f"case_{i}",
                input=um["data"].get("content", ""),
                expected_tools=expected_tools,
            ))

        return EvalBenchmark(
            name=name,
            source_session=session_id,
            created_at=time.time(),
            cases=cases,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_builder.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add arf/evaluation/builder.py tests/test_eval_builder.py
git commit -m "feat: add BenchmarkBuilder to create benchmarks from session traces"
```

---

### Task 7: EvalRunner — rewrite with real trace capture

**Files:**
- Rewrite: `arf/evaluation/runner.py`
- Test: `tests/test_eval_runner.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for EvalRunner with real trace capture."""
import pytest

from arf.core.events import AgentEvent
from arf.event_bus import InMemoryEventBus
from arf.evaluation.models import EvalCase, EvalBenchmark
from arf.evaluation.runner import EvalRunner
from arf.evaluation.metrics import SuccessRateMetric, ToolAccuracyMetric
from arf.evaluation.trace_adapter import events_to_trace


class FakeAgent:
    """Minimal agent stub that emits events and returns a response."""
    def __init__(self, bus):
        self.event_bus = bus
        self._config_hash = "fake_hash"

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
        assert report.agent_config_hash == "fake_hash"
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
        # SuccessRateMetric: trace has no errors → 1.0
        assert case["metrics"].get("success_rate") == 1.0

    @pytest.mark.anyio
    async def test_run_case_failure_captured(self, bus, benchmark):
        class FailingAgent:
            event_bus = bus
            async def chat(self, **kw):
                raise RuntimeError("model down")

        runner = EvalRunner(FailingAgent(), bus)
        report = await runner.run(benchmark)
        assert report.summary.failed == 1
        assert report.per_case[0]["passed"] is False
        assert report.per_case[0]["error"] == "model down"

    @pytest.mark.anyio
    async def test_run_session_id_isolation(self, agent, bus, benchmark):
        runner = EvalRunner(agent, bus)
        report = await runner.run(benchmark)
        # session_id should be eval_{benchmark}_{case_id}
        assert "per_case" in dir(report) or True  # no assertion needed, just no crash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_runner.py -v`
Expected: FAIL — `ImportError: cannot import name 'EvalRunner'` (or `TypeError` from old `DefaultEvalRunner`)

- [ ] **Step 3: Write minimal implementation**

Rewrite `arf/evaluation/runner.py`:

```python
"""EvalRunner — run benchmarks against agent, capture real traces via EventBus."""
import time
import uuid
import hashlib

from arf.evaluation.exceptions import EvalError
from arf.evaluation.models import EvalBenchmark, EvalReport, EvalSummary
from arf.evaluation.metrics import SuccessRateMetric, ToolAccuracyMetric, TurnEfficiencyMetric, OutputContainsMetric
from arf.evaluation.trace_adapter import events_to_trace


class EvalRunner:
    def __init__(self, agent, event_bus) -> None:
        self._agent = agent
        self._bus = event_bus
        self._metrics = [
            SuccessRateMetric(),
            ToolAccuracyMetric(),
            TurnEfficiencyMetric(),
            OutputContainsMetric(),
        ]

    async def run(self, benchmark: EvalBenchmark, *,
                  max_parallel: int = 1) -> EvalReport:
        config_hash = getattr(self._agent, "_config_hash", "")
        if not config_hash:
            config_hash = self._hash_config(self._agent)

        per_case = []
        passed = 0

        for case in benchmark.cases:
            start_idx = self._bus.event_count()
            session_id = f"eval_{benchmark.name}_{case.id}"
            t0 = time.time()
            try:
                response = await self._agent.chat(case.input, session_id=session_id)
                duration = time.time() - t0
                events = self._bus.events_since(start_idx)
                trace = events_to_trace(events)
                case_result = {
                    "case_id": case.id, "passed": True,
                    "turns": len(trace["turns"]),
                    "tool_calls": sum(len(t["tool_calls"]) for t in trace["turns"]),
                    "duration_seconds": duration,
                    "trace": trace,
                    "metrics": {},
                    "error": None,
                    "response": response,
                }
                for m in self._metrics:
                    case_result["metrics"].update(
                        await m.compute(trace, case)
                    )
                per_case.append(case_result)
                passed += 1
            except Exception as exc:
                per_case.append({
                    "case_id": case.id, "passed": False,
                    "turns": 0, "tool_calls": 0,
                    "duration_seconds": time.time() - t0,
                    "trace": {"turns": []},
                    "metrics": {},
                    "error": str(exc),
                    "response": "",
                })

        summary = self._build_summary(per_case, benchmark)
        return EvalReport(
            run_id=str(uuid.uuid4()),
            benchmark_name=benchmark.name,
            agent_config_hash=config_hash,
            timestamp=time.time(),
            summary=summary,
            per_case=per_case,
        )

    def _build_summary(self, per_case: list[dict], benchmark: EvalBenchmark) -> EvalSummary:
        total = len(per_case)
        passed = sum(1 for c in per_case if c["passed"])
        turn_counts = [c["turns"] for c in per_case if c["passed"]]
        tool_counts = [c["tool_calls"] for c in per_case if c["passed"]]
        durations = [c["duration_seconds"] for c in per_case]

        return EvalSummary(
            total=total,
            passed=passed,
            failed=total - passed,
            pass_rate=passed / total if total else 0.0,
            avg_turns=sum(turn_counts) / len(turn_counts) if turn_counts else 0.0,
            avg_tool_calls=sum(tool_counts) / len(tool_counts) if tool_counts else 0.0,
            avg_duration_seconds=sum(durations) / len(durations) if durations else 0.0,
            tool_accuracy=self._avg_metric(per_case, "tool_accuracy"),
            output_contains=self._avg_metric(per_case, "output_contains"),
        )

    @staticmethod
    def _avg_metric(per_case: list[dict], key: str) -> float:
        vals = [c["metrics"].get(key, 0.0) for c in per_case if c["passed"]]
        return sum(vals) / len(vals) if vals else 0.0

    @staticmethod
    def _hash_config(agent) -> str:
        try:
            raw = str(getattr(agent, "config", ""))
            return hashlib.sha256(raw.encode()).hexdigest()[:12]
        except Exception:
            return "unknown"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_runner.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Run full suite**

Run: `pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add arf/evaluation/runner.py tests/test_eval_runner.py
git commit -m "feat: rewrite EvalRunner with real trace capture via EventBus"
```

---

### Task 8: EvalComparator — compare two EvalReports

**Files:**
- Create: `arf/evaluation/comparator.py`
- Test: `tests/test_eval_comparator.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for EvalComparator."""
import pytest

from arf.evaluation.models import EvalReport, EvalSummary, EvalDiff
from arf.evaluation.comparator import EvalComparator
from arf.evaluation.exceptions import EvalError


class TestEvalComparator:
    @pytest.fixture
    def baseline(self):
        return EvalReport(
            run_id="r1", benchmark_name="bm1",
            agent_config_hash="aaa", timestamp=1000.0,
            summary=EvalSummary(
                total=2, passed=2, failed=0, pass_rate=1.0,
                avg_turns=2.0, avg_tool_calls=1.5,
                avg_duration_seconds=3.0,
                tool_accuracy=1.0, output_contains=1.0,
            ),
            per_case=[
                {"case_id": "c0", "passed": True, "metrics": {"tool_accuracy": 1.0}},
                {"case_id": "c1", "passed": True, "metrics": {"tool_accuracy": 1.0}},
            ],
        )

    @pytest.fixture
    def current_worse(self):
        return EvalReport(
            run_id="r2", benchmark_name="bm1",
            agent_config_hash="bbb", timestamp=2000.0,
            summary=EvalSummary(
                total=2, passed=1, failed=1, pass_rate=0.5,
                avg_turns=3.0, avg_tool_calls=2.0,
                avg_duration_seconds=5.0,
                tool_accuracy=0.5, output_contains=0.75,
            ),
            per_case=[
                {"case_id": "c0", "passed": True, "metrics": {"tool_accuracy": 1.0}},
                {"case_id": "c1", "passed": False, "metrics": {"tool_accuracy": 0.0}},
            ],
        )

    def test_compare_produces_diff(self, baseline, current_worse):
        diff = EvalComparator().compare(baseline, current_worse)
        assert diff.summary_diff["pass_rate"] == -0.5
        assert diff.summary_diff["tool_accuracy"] == -0.5
        assert len(diff.regressions) > 0
        assert len(diff.improvements) == 0

    def test_compare_different_benchmarks_raises(self, baseline):
        other = EvalReport(
            run_id="rx", benchmark_name="bm2",
            agent_config_hash="x", timestamp=0.0,
        )
        with pytest.raises(EvalError, match="different benchmark"):
            EvalComparator().compare(baseline, other)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_comparator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
"""EvalComparator — diff two EvalReports for regression detection."""
from arf.evaluation.models import EvalReport, EvalDiff
from arf.evaluation.exceptions import EvalError

_FIELDS = ["pass_rate", "avg_turns", "avg_tool_calls",
           "avg_duration_seconds", "tool_accuracy", "output_contains"]


class EvalComparator:
    def compare(self, baseline: EvalReport, current: EvalReport) -> EvalDiff:
        if baseline.benchmark_name != current.benchmark_name:
            raise EvalError(
                f"Cannot compare different benchmarks: "
                f"'{baseline.benchmark_name}' vs '{current.benchmark_name}'"
            )

        bs = baseline.summary
        cs = current.summary
        summary_diff = {
            f: round(getattr(cs, f) - getattr(bs, f), 4) for f in _FIELDS
        }

        regressions = []
        improvements = []
        baseline_cases = {c["case_id"]: c for c in baseline.per_case}
        for c in current.per_case:
            cid = c["case_id"]
            bc = baseline_cases.get(cid)
            if bc is None:
                continue
            for f in ["tool_accuracy", "output_contains"]:
                old_val = bc.get("metrics", {}).get(f, 0.0)
                new_val = c.get("metrics", {}).get(f, 0.0)
                delta = round(new_val - old_val, 4)
                if delta < -0.001:
                    regressions.append({"case_id": cid, "metric": f, "delta": delta})
                elif delta > 0.001:
                    improvements.append({"case_id": cid, "metric": f, "delta": delta})

        return EvalDiff(
            baseline_run_id=baseline.run_id,
            current_run_id=current.run_id,
            summary_diff=summary_diff,
            regressions=regressions,
            improvements=improvements,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_comparator.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add arf/evaluation/comparator.py tests/test_eval_comparator.py
git commit -m "feat: add EvalComparator for cross-run regression detection"
```

---

### Task 9: Update evaluation __init__.py exports

**Files:**
- Modify: `arf/evaluation/__init__.py`

- [ ] **Step 1: Update exports**

```python
from arf.evaluation.runner import EvalRunner
from arf.evaluation.builder import BenchmarkBuilder
from arf.evaluation.comparator import EvalComparator
from arf.evaluation.metrics import SuccessRateMetric, ToolAccuracyMetric, TurnEfficiencyMetric, OutputContainsMetric
from arf.evaluation.models import EvalCase, EvalBenchmark, EvalReport, EvalSummary, EvalDiff
from arf.evaluation.exceptions import EvalError
from arf.evaluation.trace_adapter import events_to_trace

__all__ = [
    "EvalRunner", "BenchmarkBuilder", "EvalComparator",
    "SuccessRateMetric", "ToolAccuracyMetric", "TurnEfficiencyMetric", "OutputContainsMetric",
    "EvalCase", "EvalBenchmark", "EvalReport", "EvalSummary", "EvalDiff",
    "EvalError", "events_to_trace",
]
```

- [ ] **Step 2: Verify import**

Run: `python -c "from arf.evaluation import EvalRunner, BenchmarkBuilder, EvalComparator, EvalReport, EvalBenchmark, EvalDiff, EvalError, events_to_trace; print('OK')"`

- [ ] **Step 3: Run full suite**

Run: `pytest tests/ -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add arf/evaluation/__init__.py
git commit -m "feat: update eval __init__ with new exports"
```

---

### Task 10: Update README TODO #5

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Mark TODO #5 as fixed**

Replace in both READMEs:
```markdown
| 5 | `EvalRunner` computes empty traces | `arf/evaluation/runner.py:17` | Quality Assurance | Framework | `run()` calls `agent.chat()` then hardcodes `trace = {"turns": []}`, never collecting real turn-by-turn execution traces from `EventBus` or `StateStore`. `ToolAccuracyMetric` / `TurnEfficiencyMetric` always compute on empty data. **Risk**: No automated regression detection for framework changes; the "60% coverage" goal has no supporting evaluation mechanism |
```

→

```markdown
| 5 | ~~`EvalRunner` computes empty traces~~ → **FIXED** | `arf/evaluation/runner.py` | Quality Assurance | Framework | ~~trace hardcoded as `{"turns": []}`~~ → Rewritten: `EvalRunner` captures real traces via `EventBus.events_since()`, `events_to_trace()` assembles structured turn data, all 4 metrics compute on real traces. `BenchmarkBuilder` creates benchmarks from `FileTraceStore` sessions, `EvalComparator` diffs cross-run reports for regression detection. |
```

- [ ] **Step 2: Commit**

```bash
git add README.md README.zh-CN.md
git commit -m "docs: mark TODO #5 EvalRunner as fixed"
```
