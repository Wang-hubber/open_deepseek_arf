"""BenchmarkBuilder — create EvalBenchmark from trace sessions."""
import time

from arf.evaluation.exceptions import EvalError
from arf.evaluation.models import EvalCase, EvalBenchmark


class BenchmarkBuilder:
    """Build EvalBenchmark datasets from recorded trajectories.

    Takes a TracePlugin instance and reads session trace files to
    construct rich EvalCases with golden_trajectory, expected_tools,
    expected_output_contains, and max_turns.
    """

    def __init__(self, trace_plugin):
        self._trace = trace_plugin

    def build(self, session_id: str, name: str) -> EvalBenchmark:
        events = self._trace.read_trace(session_id)
        if not events:
            raise EvalError(f"Session '{session_id}' not found in trace store")

        # Find user_input events and build cases
        user_events = [e for e in events if e.get("type") == "user_input"]
        if not user_events:
            raise EvalError(f"No user messages found in session '{session_id}'")

        cases: list[EvalCase] = []
        for i, ue in enumerate(user_events):
            turn = ue.get("turn", i + 1)

            # Find next user turn boundary
            next_user_turn = None
            for ue2 in user_events:
                if ue2.get("turn", 0) > turn:
                    next_user_turn = ue2.get("turn")
                    break

            # Collect tool calls in this case's turns
            max_turn = next_user_turn if next_user_turn else max(
                (e.get("turn", 0) for e in events), default=turn
            )
            tool_names: list[str] = []
            for e in events:
                et = e.get("turn", 0)
                if et >= turn and (next_user_turn is None or et < next_user_turn):
                    if e.get("type") == "tool_call_start":
                        tn = e.get("data", {}).get("tool_name", "")
                        if tn:
                            tool_names.append(tn)

            # Build golden trajectory turns
            golden_turns = self._build_golden_turns(events, turn, next_user_turn)

            # Extract expected_output_contains from final assistant content
            expected_output = self._extract_output_contains(
                events, turn, next_user_turn
            )

            cases.append(EvalCase(
                id=f"case_{i}",
                input=ue.get("data", {}).get("content", ""),
                expected_tools=tool_names if tool_names else None,
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
    def _build_golden_turns(events, start_turn, end_turn):
        """Extract golden trajectory turns between start_turn and end_turn."""
        max_turn = end_turn if end_turn else max(
            (e.get("turn", 0) for e in events), default=start_turn
        )
        turns = []
        for t in range(start_turn, max_turn + 1):
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
            # last model_call_end after tools = final response
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
    def _extract_output_contains(events, start_turn, end_turn):
        """Extract keywords from final assistant content for expected_output_contains."""
        max_turn = end_turn if end_turn else max(
            (e.get("turn", 0) for e in events), default=start_turn
        )
        for e in reversed(events):
            if e.get("turn") == max_turn and e.get("type") == "model_call_end":
                content = e.get("data", {}).get("content", "")
                if content:
                    words = content.split()
                    if len(words) >= 3:
                        return [" ".join(words[:3])]
                    return [content[:50]]
        return None
