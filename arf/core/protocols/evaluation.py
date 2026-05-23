"""Protocols for evaluation domain."""
from typing import Protocol
from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    input: str
    expected_tools: list[str] | None = None
    expected_output_contains: list[str] | None = None
    max_turns: int | None = None


@dataclass
class EvalDataset:
    name: str
    cases: list[EvalCase] = field(default_factory=list)


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


@dataclass
class EvalReport:
    run_id: str
    dataset_name: str
    agent_config_hash: str
    timestamp: float
    summary: EvalSummary = field(default_factory=EvalSummary)
    per_case: list[dict] = field(default_factory=list)
    comparison: dict | None = None


class MetricCalculator(Protocol):
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]: ...


class EvalRunner(Protocol):
    async def run(
        self, agent, dataset: EvalDataset, metrics: list[MetricCalculator],
        *, baseline: EvalReport | None = None, max_parallel: int = 1,
    ) -> EvalReport: ...
