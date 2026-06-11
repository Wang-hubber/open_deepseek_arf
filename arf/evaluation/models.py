"""Eval data models with JSON serialization."""
import json
from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    input: str
    expected_tools: list[str] | None = None
    expected_tool_calls: list[dict] | None = None  # indexed [{name, params?, result?}]
    expected_output_contains: list[str] | None = None
    max_turns: int | None = None
    golden_trajectory: dict | None = None  # full golden trajectory from trace


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
                    **({"expected_tool_calls": c.expected_tool_calls} if c.expected_tool_calls else {}),
                    **({"expected_output_contains": c.expected_output_contains} if c.expected_output_contains else {}),
                    **({"max_turns": c.max_turns} if c.max_turns is not None else {}),
                    **({"golden_trajectory": c.golden_trajectory}
                       if c.golden_trajectory else {}),
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
                    expected_tool_calls=c.get("expected_tool_calls"),
                    expected_output_contains=c.get("expected_output_contains"),
                    max_turns=c.get("max_turns"),
                    golden_trajectory=c.get("golden_trajectory"),
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
    # New metric-specific averages
    tool_call_accuracy: float = 0.0
    turn_efficiency: float = 0.0
    success_rate: float = 0.0
    tool_call_result_llm: float | None = None  # 0-1 LLM judge
    output_quality: float | None = None  # 1-5 LLM judge
    trajectory_similarity: float | None = None  # 1-5 LLM judge


@dataclass
class EvalReport:
    run_id: str
    benchmark_name: str
    agent_config_hash: str
    timestamp: float
    summary: EvalSummary = field(default_factory=EvalSummary)
    per_case: list[dict] = field(default_factory=list)
    judge_model: str = ""
    metrics_enabled: list[str] = field(default_factory=list)
    mode: str = "online"
    snapshot_hash: str = ""

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
                "tool_call_accuracy": self.summary.tool_call_accuracy,
                "turn_efficiency": self.summary.turn_efficiency,
                "success_rate": self.summary.success_rate,
                "tool_call_result_llm": self.summary.tool_call_result_llm,
                "output_quality": self.summary.output_quality,
                "trajectory_similarity": self.summary.trajectory_similarity,
            },
            "per_case": self.per_case,
            "judge_model": self.judge_model,
            "metrics_enabled": self.metrics_enabled,
            "mode": self.mode,
            "snapshot_hash": self.snapshot_hash,
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
                tool_call_accuracy=s.get("tool_call_accuracy", 0.0),
                turn_efficiency=s.get("turn_efficiency", 0.0),
                success_rate=s.get("success_rate", 0.0),
                tool_call_result_llm=s.get("tool_call_result_llm"),
                output_quality=s.get("output_quality"),
                trajectory_similarity=s.get("trajectory_similarity"),
            ),
            per_case=data.get("per_case", []),
            judge_model=data.get("judge_model", ""),
            metrics_enabled=data.get("metrics_enabled", []),
            mode=data.get("mode", "online"),
            snapshot_hash=data.get("snapshot_hash", ""),
        )


@dataclass
class EvalDiff:
    baseline_run_id: str
    current_run_id: str
    summary_diff: dict = field(default_factory=dict)
    regressions: list[dict] = field(default_factory=list)
    improvements: list[dict] = field(default_factory=list)


@dataclass
class JudgeModelConfig:
    """OpenAI API-compatible judge LLM configuration.

    Independent of the ARF ModelAdapter — the judge should not share
    a model with the agent under evaluation.
    """
    api_base: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model: str = "gpt-4"
    temperature: float = 0.0
    max_tokens: int = 2000


@dataclass
class EvalConfig:
    """Evaluation runner configuration."""
    benchmark_path: str = ""
    trace_dir: str = "./data/traces"
    judge: JudgeModelConfig | None = None
    metrics: dict[str, bool] = field(default_factory=lambda: {
        "tool_call_accuracy": True,
        "tool_call_result_llm": False,
        "turn_efficiency": True,
        "success_rate": True,
        "output_quality": False,
        "trajectory_similarity": False,
    })
    mode: str = "online"
    trace_session_ids: list[str] = field(default_factory=list)
    output_path: str | None = None
    timeout_per_case: float = 300.0

    def requires_judge(self) -> bool:
        return any([
            self.metrics.get("output_quality", False),
            self.metrics.get("trajectory_similarity", False),
            self.metrics.get("tool_call_result_llm", False),
        ])

    def validate(self) -> None:
        if self.requires_judge() and self.judge is None:
            raise ValueError(
                "LLM-as-judge metrics enabled but no judge model configured"
            )
        if self.mode == "offline" and not self.trace_session_ids:
            raise ValueError("Offline mode requires trace_session_ids")
