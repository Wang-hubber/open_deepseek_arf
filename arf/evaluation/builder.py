"""BenchmarkBuilder — create EvalBenchmark from trace sessions."""
import time

from arf.evaluation.exceptions import EvalError
from arf.evaluation.models import EvalCase, EvalBenchmark


class BenchmarkBuilder:
    """Build EvalBenchmark datasets from recorded trajectories.

    Takes a TracePlugin instance and reads session trace files to
    construct rich EvalCases with golden_trajectory, expected_tools,
    expected_output_contains, and max_turns.

    Cases are delimited by user_input event positions in the event list,
    not by turn numbers — this avoids boundary misalignment when turn
    numbers don't match conversation boundaries.
    """

    def __init__(self, trace_plugin):
        self._trace = trace_plugin

    def build(self, session_id: str, name: str) -> EvalBenchmark:
        events = self._trace.read_trace(session_id)
        if not events:
            raise EvalError(f"Session '{session_id}' not found in trace store")

        # Find user_input event indices as case boundaries
        user_indices = [
            i for i, e in enumerate(events) if e.get("type") == "user_input"
        ]
        if not user_indices:
            raise EvalError(f"No user messages found in session '{session_id}'")

        cases: list[EvalCase] = []
        for i, ui in enumerate(user_indices):
            start = ui
            end = user_indices[i + 1] if i + 1 < len(user_indices) else len(events)
            case_events = events[start:end]

            # Collect tool names from this slice
            tool_names: list[str] = []
            for e in case_events:
                if e.get("type") == "tool_call_start":
                    tn = e.get("data", {}).get("tool_name", "")
                    if tn:
                        tool_names.append(tn)

            # Build golden trajectory from this slice
            golden_turns = self._build_golden_turns(case_events)

            # Extract expected_tool_calls from golden trajectory
            expected_tool_calls = self._build_expected_tool_calls(golden_turns)

            # Extract expected_output_contains from final model response
            expected_output = self._extract_output_contains(case_events)

            cases.append(EvalCase(
                id=f"case_{i}",
                input=events[ui].get("data", {}).get("content", ""),
                expected_tools=tool_names if tool_names else None,
                expected_tool_calls=expected_tool_calls if expected_tool_calls else None,
                expected_output_contains=expected_output,
                max_turns=len(golden_turns) if golden_turns else None,
                golden_trajectory={"turns": golden_turns} if golden_turns else None,
            ))

        return EvalBenchmark(
            name=name,
            source_session=session_id,
            created_at=time.time(),
            cases=cases,
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
    def _build_expected_tool_calls(golden_turns):
        """Build expected_tool_calls list from golden trajectory turns.

        Pairs assistant.tool_calls[i] with tool_results[i] by index.
        Returns [{"name": ..., "params": {...}, "result": "..."}, ...].
        """
        calls = []
        for turn in golden_turns:
            tool_calls = turn.get("assistant", {}).get("tool_calls", [])
            tool_results = turn.get("tool_results", [])
            for i, tc in enumerate(tool_calls):
                info: dict = {
                    "name": tc.get("name", ""),
                    "params": tc.get("params", {}),
                }
                if i < len(tool_results):
                    tr = tool_results[i]
                    info["result"] = tr.get("result", "")
                    info["success"] = tr.get("success", False)
                calls.append(info)
        return calls

    @staticmethod
    def _extract_output_contains(events):
        """Extract keywords from the last model_call_end in the event slice."""
        for e in reversed(events):
            if e.get("type") == "model_call_end":
                content = e.get("data", {}).get("content", "")
                if content:
                    words = content.split()
                    if len(words) >= 3:
                        return [" ".join(words[:3])]
                    return [content[:50]]
        return None
