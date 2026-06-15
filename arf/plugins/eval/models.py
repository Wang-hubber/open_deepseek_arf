"""Eval data models with JSON serialization."""
import json
from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    input: str
    session_id: str | None = None
    expected_reasoning: list[str] = field(default_factory=list)
    expected_execution: list[dict] = field(default_factory=list)
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
    trace_snapshot_path: str | None = None  # relative to benchmark JSON, frozen trace copy

    def to_json(self, path: str) -> None:
        data = {
            "name": self.name,
            "source_session": self.source_session,
            "created_at": self.created_at,
            **({"trace_snapshot_path": self.trace_snapshot_path} if self.trace_snapshot_path else {}),
            "cases": [
                {
                    "id": c.id,
                    "input": c.input,
                    **({"session_id": c.session_id} if c.session_id else {}),
                    **({"source_round": c.source_round} if c.source_round is not None else {}),
                    **({"expected_reasoning": c.expected_reasoning} if c.expected_reasoning else {}),
                    **({"expected_execution": c.expected_execution} if c.expected_execution else {}),
                    **({"expected_output_contains": c.expected_output_contains} if c.expected_output_contains else {}),
                    **({"max_turns": c.max_turns} if c.max_turns is not None else {}),
                    **({"feedback": c.feedback} if c.feedback is not None else {}),
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
            trace_snapshot_path=data.get("trace_snapshot_path"),
            cases=[
                EvalCase(
                    id=c["id"],
                    input=c["input"],
                    session_id=c.get("session_id"),
                    source_round=c.get("source_round"),
                    expected_reasoning=c.get("expected_reasoning", []),
                    expected_execution=c.get("expected_execution", []),
                    expected_output_contains=c.get("expected_output_contains", []),
                    max_turns=c.get("max_turns"),
                    feedback=c.get("feedback"),
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
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_duration_seconds: float = 0.0
    tool_accuracy: float = 0.0
    output_contains: float = 0.0
    # New metric-specific averages
    tool_call_accuracy: float = 0.0
    turn_efficiency: float = 0.0
    success_rate: float = 0.0
    tool_call_result_llm: float | None = None  # 0-1 LLM judge
    output_quality: float | None = None  # 1-5 LLM judge
    trajectory_similarity: float | None = None  # 1-5 LLM judge
    execution_accuracy: float = 0.0
    reasoning_similarity: float | None = None


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
                "total_tokens_in": self.summary.total_tokens_in,
                "total_tokens_out": self.summary.total_tokens_out,
                "total_duration_seconds": self.summary.total_duration_seconds,
                "tool_accuracy": self.summary.tool_accuracy,
                "output_contains": self.summary.output_contains,
                "tool_call_accuracy": self.summary.tool_call_accuracy,
                "turn_efficiency": self.summary.turn_efficiency,
                "success_rate": self.summary.success_rate,
                "tool_call_result_llm": self.summary.tool_call_result_llm,
                "output_quality": self.summary.output_quality,
                "trajectory_similarity": self.summary.trajectory_similarity,
                "execution_accuracy": self.summary.execution_accuracy,
                "reasoning_similarity": self.summary.reasoning_similarity,
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
                total_tokens_in=s.get("total_tokens_in", 0),
                total_tokens_out=s.get("total_tokens_out", 0),
                total_duration_seconds=s.get("total_duration_seconds", 0.0),
                tool_accuracy=s.get("tool_accuracy", 0.0),
                output_contains=s.get("output_contains", 0.0),
                tool_call_accuracy=s.get("tool_call_accuracy", 0.0),
                turn_efficiency=s.get("turn_efficiency", 0.0),
                success_rate=s.get("success_rate", 0.0),
                tool_call_result_llm=s.get("tool_call_result_llm"),
                output_quality=s.get("output_quality"),
                trajectory_similarity=s.get("trajectory_similarity"),
                execution_accuracy=s.get("execution_accuracy", 0.0),
                reasoning_similarity=s.get("reasoning_similarity"),
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
    """Judge semantic configuration — model connection info comes from model_defs."""
    system_prompt: str = (
        "You are an expert evaluator for AI agent behavior. Your role is to "
        "compare an agent's actual output against a reference (golden) standard. "
        "You are impartial, precise, and consistent. Ground every judgment in the "
        "specific content provided — never speculate about missing information, "
        "and clearly state when a comparison cannot be reliably made. "
        "Every evaluation must be reproducible: another evaluator reading your "
        "reasoning should arrive at the same conclusion. "
        "Always respond with valid JSON only, no markdown fences or extra text."
    )
    response_format: dict | None = None


@dataclass
class EvalConfig:
    """Evaluation runner configuration."""
    benchmark_path: str = ""
    data_dir: str = "./data"
    eval_dir: str = "./eval"
    judge: JudgeModelConfig | None = None
    judge_model: "ResolvedModelConfig | None" = None  # model connection from plugins_config.eval
    metrics: dict[str, bool] = field(default_factory=lambda: {
        "tool_call_accuracy": True,
        "tool_call_result_llm": False,
        "turn_efficiency": True,
        "success_rate": True,
        "execution_accuracy": True,
        "reasoning_similarity": False,
        "output_quality": False,
        "trajectory_similarity": False,
        "output_contains": True,
    })
    mode: str = "online"
    trace_session_ids: list[str] = field(default_factory=list)
    output_path: str | None = None
    timeout_per_case: float = 300.0
    prompts: dict[str, str] = field(default_factory=dict)
    # keys: tool_call_result_llm, output_quality, trajectory_similarity,
    #        output_quality_free, trajectory_similarity_free

    def requires_judge(self) -> bool:
        return any([
            self.metrics.get("output_quality", False),
            self.metrics.get("trajectory_similarity", False),
            self.metrics.get("tool_call_result_llm", False),
            self.metrics.get("reasoning_similarity", False),
        ])

    def validate(self) -> None:
        if self.requires_judge():
            if self.judge is None:
                raise ValueError(
                    "LLM-as-judge metrics enabled but no judge configured"
                )
            if self.judge_model is None:
                raise ValueError(
                    "LLM-as-judge metrics enabled but no judge_model configured. "
                    "Set plugins_config.eval in agent.yaml or use --judge-* CLI flags."
                )
        if self.mode == "offline" and not self.trace_session_ids:
            raise ValueError("Offline mode requires trace_session_ids")
