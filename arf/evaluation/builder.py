"""BenchmarkBuilder — create EvalBenchmark from FileTraceStore session."""
import time

from arf.evaluation.exceptions import EvalError
from arf.evaluation.models import EvalCase, EvalBenchmark


class BenchmarkBuilder:
    def __init__(self, trace_store):
        self._store = trace_store

    def build(self, session_id: str, name: str) -> EvalBenchmark:
        events = self._store.load(session_id)
        if not events:
            raise EvalError(f"Session '{session_id}' not found in trace store")

        cases: list[EvalCase] = []
        user_msgs = [e for e in events if e.get("type") == "user_input"]
        if not user_msgs:
            raise EvalError(f"No user messages found in session '{session_id}'")

        for i, um in enumerate(user_msgs):
            turn = um.get("turn", 0)
            tools_in_turn = [
                e.get("data", {}).get("tool_name", "")
                for e in events
                if e.get("type") == "tool_call_start" and e.get("turn") == turn
            ]
            expected_tools = tools_in_turn if tools_in_turn else None
            cases.append(EvalCase(
                id=f"case_{i}",
                input=um["data"].get("content", ""),
                expected_tools=expected_tools,
            ))

        return EvalBenchmark(
            name=name,
            source_session=session_id,
            created_at=time.time(),
            cases=cases,
        )
