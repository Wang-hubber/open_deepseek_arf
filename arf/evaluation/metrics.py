"""Built-in evaluation metrics."""
from arf.core.protocols.evaluation import EvalCase


class SuccessRateMetric:
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]:
        errors = sum(1 for t in trace.get("turns", []) if t.get("error"))
        return {"success_rate": 0.0 if errors > 0 else 1.0}


class ToolAccuracyMetric:
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]:
        if not expected.expected_tools:
            return {"tool_accuracy": 1.0}
        actual = []
        for t in trace.get("turns", []):
            for tc in t.get("tool_calls", []):
                actual.append(tc.get("tool_name", ""))
        if not actual:
            return {"tool_accuracy": 0.0}
        matches = sum(1 for e, a in zip(expected.expected_tools, actual) if e == a)
        return {"tool_accuracy": matches / len(expected.expected_tools)}


class TurnEfficiencyMetric:
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]:
        return {"turn_count": float(len(trace.get("turns", [])))}


class OutputContainsMetric:
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]:
        if not expected.expected_output_contains:
            return {"output_contains": 1.0}
        last_output = ""
        for t in reversed(trace.get("turns", [])):
            last_output = t.get("model_output", "")
            if last_output:
                break
        matches = sum(1 for kw in expected.expected_output_contains if kw.lower() in last_output.lower())
        return {"output_contains": matches / len(expected.expected_output_contains) if expected.expected_output_contains else 1.0}
