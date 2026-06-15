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

            source_round = events[ui].get("round", 0)

            golden_turns = self._build_golden_turns(case_events)
            expected_execution = self._build_expected_execution(golden_turns)

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
                expected_reasoning = ["[待标注] 该轮预期推理步骤..."]
                expected_output = ["[待标注] 该轮预期输出关键词..."]
            else:
                expected_reasoning = []
                expected_output = []

            cases.append(EvalCase(
                id=f"case_{i}",
                input=events[ui].get("data", {}).get("content", ""),
                session_id=session_id,
                source_round=source_round,
                expected_reasoning=expected_reasoning,
                expected_execution=expected_execution,
                expected_output_contains=expected_output,
                max_turns=len(golden_turns) if golden_turns else None,
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
    def _build_golden_turns(events):
        """Extract golden trajectory turns from a slice of events.

        Groups events by turn number within the slice. Each turn produces
        one entry with assistant content, tool_calls, tool_results, and
        assistant_final.
        """
        turn_set = sorted({e.get("turn", 0) for e in events if e.get("turn", 0) > 0})
        turns = []
        for t in turn_set:
            turn_events = [e for e in events if e.get("turn") == t]
            turn_data = BenchmarkBuilder._extract_turn_data(t, turn_events)
            if turn_data:
                turns.append(turn_data)
        return turns

    @staticmethod
    def _extract_turn_data(turn_num, events):
        """Extract assistant, tool_results, and assistant_final from turn events."""
        assistant_content = ""
        tool_calls = []
        tool_results = []
        assistant_final = {}

        for e in events:
            etype = e.get("type", "")
            data = e.get("data", {})

            if etype == "model_call_end":
                if data.get("content") and not assistant_content:
                    assistant_content = data["content"]
                for tc in data.get("tool_calls", []):
                    tool_calls.append({
                        "name": tc.get("name", ""),
                        "params": tc.get("params", {}),
                    })
            elif etype == "tool_call_end":
                tool_results.append({
                    "tool_name": data.get("tool_name", ""),
                    "result": data.get("result", ""),
                    "success": data.get("success", False),
                })

        if tool_results:
            for e in reversed(events):
                if e.get("type") == "model_call_end":
                    content = e.get("data", {}).get("content", "")
                    if content:
                        assistant_final = {"content": content}
                        break

        if not assistant_content and not tool_results:
            return None

        return {
            "turn": turn_num,
            "assistant": {
                "content": assistant_content,
                "tool_calls": tool_calls,
            },
            "tool_results": tool_results,
            "assistant_final": assistant_final,
        }

    @staticmethod
    def _build_expected_execution(golden_turns):
        """Build expected_execution list from golden trajectory turns."""
        entries = []
        for turn in golden_turns:
            tool_calls = turn.get("assistant", {}).get("tool_calls", [])
            tool_results = turn.get("tool_results", [])
            for i, tc in enumerate(tool_calls):
                info: dict = {
                    "type": "tool",
                    "name": tc.get("name", ""),
                    "params": tc.get("params", {}),
                }
                if i < len(tool_results):
                    tr = tool_results[i]
                    result_text = tr.get("result", "")
                    if isinstance(result_text, str) and len(result_text) > 200:
                        info["result_preview"] = result_text[:200] + "..."
                    elif result_text:
                        info["result_preview"] = str(result_text)
                    info["success"] = tr.get("success", False)
                entries.append(info)
        return entries

