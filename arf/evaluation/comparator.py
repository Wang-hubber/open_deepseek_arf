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
