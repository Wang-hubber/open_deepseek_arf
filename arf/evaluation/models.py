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
