"""BenchmarkBuilder — create EvalBenchmark from trace sessions."""
import json
import time
from pathlib import Path

from arf.plugins.eval.exceptions import EvalError
from arf.plugins.eval.models import EvalCase, EvalBenchmark


class BenchmarkBuilder:
    """Build EvalBenchmark datasets from recorded trajectories.

    Takes a TracePlugin instance and reads session trace files to
    construct rich EvalCases with expected_tools, expected_output_contains,
    and max_turns. A frozen trace snapshot is written alongside the benchmark
    so later session activity doesn't corrupt the golden reference.
    """

    def __init__(self, trace_plugin):
        self._trace = trace_plugin

    def build(self, session_id: str, name: str, *,
              benchmark_dir: str = "benchmarks",
              annotate_mode: bool = False) -> EvalBenchmark:
        events = self._trace.read_trace(session_id)
        if not events:
            raise EvalError(f"Session '{session_id}' not found in trace store")

        bm_dir = Path(benchmark_dir)
        bm_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = bm_dir / f"{name}.trace.jsonl"
        with open(snapshot_path, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        user_indices = [
            i for i, e in enumerate(events) if e.get("type") == "user_input"
        ]
        if not user_indices:
            raise EvalError(f"No user messages found in session '{session_id}'")

        # Collect user_annotation events by target round
        annotations_by_round: dict[int, list[dict]] = {}
        for e in events:
            if e.get("type") == "user_annotation":
                r = e.get("data", {}).get("round", 0)
                annotations_by_round.setdefault(r, []).append(e)

        cases: list[EvalCase] = []
        for i, ui in enumerate(user_indices):
            start = ui
            end = user_indices[i + 1] if i + 1 < len(user_indices) else len(events)
            case_events = events[start:end]

            source_round = i  # derive from user_input index, matching engine's 0-based interaction_round

            tool_names = self._collect_tool_names(case_events)
            turns_with_events = {e.get("turn") or 0 for e in case_events
                                 if (e.get("turn") or 0) > 0}

            # Feedback: latest user_annotation for this round
            feedback = None
            round_annotations = annotations_by_round.get(source_round, [])
            if round_annotations:
                latest = max(round_annotations, key=lambda e: e.get("timestamp", 0))
                data = latest.get("data", {})
                feedback = {
                    "rating": data.get("feedback", ""),
                    "reason": data.get("reason", ""),
                    "annotated_at": data.get("annotated_at", ""),
                }

            if annotate_mode:
                expected_output = ["[待标注] 该轮预期输出关键词..."]
                expected_execution = ["[待标注] 预期工具名"]
            else:
                expected_output = []
                expected_execution = tool_names

            cases.append(EvalCase(
                id=f"case_{i}",
                input=events[ui].get("data", {}).get("content", ""),
                session_id=session_id,
                source_round=source_round,
                expected_execution=expected_execution,
                expected_output_contains=expected_output,
                max_turns=len(turns_with_events) if turns_with_events else None,
                feedback=feedback,
            ))

        return EvalBenchmark(
            name=name,
            source_session=session_id,
            created_at=time.time(),
            cases=cases,
            trace_snapshot_path=str(snapshot_path),
        )

    @staticmethod
    def _collect_tool_names(events):
        """Extract ordered tool names from tool_call_start or model_call_end events."""
        names: list[str] = []
        for e in events:
            if e.get("type") == "tool_call_start":
                data = e.get("data", {})
                name = data.get("name") or data.get("tool_name", "")
                if name and name not in names:
                    names.append(name)
            elif e.get("type") == "model_call_end":
                for tc in e.get("data", {}).get("tool_calls", []):
                    name = tc.get("name", "")
                    if name and name not in names:
                        names.append(name)
        return names

