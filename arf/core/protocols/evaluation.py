"""Protocols for evaluation domain."""
from typing import Protocol
from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    input: str
    session_id: str | None = None
    expected_execution: list[str] = field(default_factory=list)
    expected_output_contains: list[str] = field(default_factory=list)
    max_turns: int | None = None
    feedback: dict | None = None
    source_round: int | None = None


@dataclass
class EvalBenchmark:
    name: str
    source_session: str | None = None
    created_at: float = 0.0
    cases: list[EvalCase] = field(default_factory=list)


# Backward-compat alias
EvalDataset = EvalBenchmark


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


@dataclass
class EvalDiff:
    baseline_run_id: str
    current_run_id: str
    summary_diff: dict = field(default_factory=dict)
    regressions: list[dict] = field(default_factory=list)
    improvements: list[dict] = field(default_factory=list)


class MetricCalculator(Protocol):
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]: ...


class BenchmarkBuilder(Protocol):
    def build(self, session_id: str, name: str) -> EvalBenchmark: ...


class EvalRunner(Protocol):
    async def run(self, benchmark: EvalBenchmark) -> EvalReport: ...


class EvalComparator(Protocol):
    def compare(self, baseline: EvalReport, current: EvalReport) -> EvalDiff: ...
