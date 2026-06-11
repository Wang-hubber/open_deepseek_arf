"""EvalRunner — run benchmarks against agent, capture real traces via EventBus."""
import time
import uuid
import hashlib

from arf.evaluation.models import EvalBenchmark, EvalReport, EvalSummary
from arf.evaluation.metrics import SuccessRateMetric, ToolCallAccuracyMetric, TurnEfficiencyMetric
from arf.evaluation.trace_adapter import events_to_trace


class EvalRunner:
    def __init__(self, agent, event_bus) -> None:
        self._agent = agent
        self._bus = event_bus
        self._metrics = [
            SuccessRateMetric(),
            ToolCallAccuracyMetric(),
            TurnEfficiencyMetric(),
        ]

    async def run(self, benchmark: EvalBenchmark) -> EvalReport:
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
                # Convert AgentEvent dataclass instances to flat dicts for metrics
                event_dicts = [
                    {"type": e.type, "data": e.data,
                     "timestamp": e.timestamp, "turn": e.turn,
                     "session_id": e.session_id}
                    for e in events
                ]
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
                        await m.compute(event_dicts, case)
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
        passed_cnt = sum(1 for c in per_case if c["passed"])
        turn_counts = [c["turns"] for c in per_case if c["passed"]]
        tool_counts = [c["tool_calls"] for c in per_case if c["passed"]]
        durations = [c["duration_seconds"] for c in per_case]

        return EvalSummary(
            total=total,
            passed=passed_cnt,
            failed=total - passed_cnt,
            pass_rate=passed_cnt / total if total else 0.0,
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
