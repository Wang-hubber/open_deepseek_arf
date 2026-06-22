"""EvalComparator — diff two EvalReports for regression detection."""
from arf.plugins.eval.models import EvalReport, EvalDiff
from arf.plugins.eval.exceptions import EvalError

_FIELDS = [
    # Basic stats
    "pass_rate", "avg_turns", "avg_tool_calls", "avg_duration_seconds",
    # Rule-based metrics
    "tool_accuracy", "output_contains",
    # LLM metrics
    "tool_call_accuracy", "turn_efficiency", "success_rate",
    "execution_accuracy", "reasoning_similarity",
    "output_quality", "trajectory_similarity",
    # Composite
    "weighted_score",
]

_PER_CASE_FIELDS = [
    "tool_accuracy", "output_contains",
    "output_quality", "trajectory_similarity", "weighted_score",
]


class EvalComparator:
    def compare(self, baseline: EvalReport, current: EvalReport) -> EvalDiff:
        if baseline.benchmark_name != current.benchmark_name:
            raise EvalError(
                f"Cannot compare different benchmarks: "
                f"'{baseline.benchmark_name}' vs '{current.benchmark_name}'"
            )

        bs = baseline.summary
        cs = current.summary

        # Dynamic field selection: only compare fields where both have non-None
        active_fields = [
            f for f in _FIELDS
            if getattr(bs, f) is not None and getattr(cs, f) is not None
        ]
        summary_diff = {
            f: round(getattr(cs, f) - getattr(bs, f), 4) for f in active_fields
        }

        regressions = []
        improvements = []
        baseline_cases = {c["case_id"]: c for c in baseline.per_case}
        for c in current.per_case:
            cid = c["case_id"]
            bc = baseline_cases.get(cid)
            if bc is None:
                continue
            for f in _PER_CASE_FIELDS:
                old_val = bc.get("metrics", {}).get(f)
                new_val = c.get("metrics", {}).get(f)
                if old_val is None or new_val is None:
                    continue
                if not isinstance(old_val, (int, float)) or not isinstance(new_val, (int, float)):
                    continue
                delta = round(new_val - old_val, 4)
                if delta < -0.001:
                    regressions.append({"case_id": cid, "metric": f, "delta": delta})
                elif delta > 0.001:
                    improvements.append({"case_id": cid, "metric": f, "delta": delta})

        return EvalDiff(
            baseline_run_id=baseline.run_id,
            current_run_id=current.run_id,
            baseline_hash=baseline.snapshot_hash,
            current_hash=current.snapshot_hash,
            summary_diff=summary_diff,
            regressions=regressions,
            improvements=improvements,
        )
