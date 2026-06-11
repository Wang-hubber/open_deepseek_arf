"""Evaluation metrics — rule-based and LLM-as-judge."""
import asyncio
import json
import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class EvalMetric(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def requires_llm(self) -> bool: ...

    async def compute(
        self,
        actual_trace: list[dict],
        golden_case: "EvalCase",
        judge: "JudgeModelConfig | None" = None,
    ) -> dict[str, float | str]: ...


class SuccessRateMetric:
    @property
    def name(self) -> str:
        return "success_rate"

    @property
    def requires_llm(self) -> bool:
        return False

    async def compute(self, actual_trace, golden_case, judge=None):
        errors = sum(1 for e in actual_trace if e.get("type") == "error")
        return {"success_rate": 0.0 if errors > 0 else 1.0}

    def compute_sync(self, actual_trace, golden_case, judge=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge))


class ToolCallAccuracyMetric:
    @property
    def name(self) -> str:
        return "tool_call_accuracy"

    @property
    def requires_llm(self) -> bool:
        return False

    async def compute(self, actual_trace, golden_case, judge=None):
        if not golden_case.expected_tools:
            return {"tool_call_accuracy": 1.0}
        actual_names = []
        for e in actual_trace:
            if e.get("type") == "tool_call_start":
                tn = e.get("data", {}).get("tool_name", "")
                if tn:
                    actual_names.append(tn)
        if not actual_names:
            return {"tool_call_accuracy": 0.0}
        matches = sum(
            1 for a, e in zip(actual_names, golden_case.expected_tools) if a == e
        )
        return {"tool_call_accuracy": matches / len(golden_case.expected_tools)}

    def compute_sync(self, actual_trace, golden_case, judge=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge))


class TurnEfficiencyMetric:
    @property
    def name(self) -> str:
        return "turn_efficiency"

    @property
    def requires_llm(self) -> bool:
        return False

    async def compute(self, actual_trace, golden_case, judge=None):
        turn_set = {e.get("turn", 0) for e in actual_trace if e.get("turn", 0) > 0}
        actual_turns = len(turn_set)
        if golden_case.max_turns:
            return {"turn_efficiency": min(1.0, golden_case.max_turns / max(actual_turns, 1))}
        return {"turn_efficiency": 1.0}

    def compute_sync(self, actual_trace, golden_case, judge=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge))


class OutputQualityMetric:
    _PROMPT = (
        "You are evaluating an AI agent's final output against a golden reference.\n\n"
        "Golden answer:\n{golden}\n\n"
        "Actual answer:\n{actual}\n\n"
        "Rate the actual answer from 1 (completely wrong) "
        "to 5 (identical quality). Consider: correctness, completeness, "
        "clarity, and helpfulness.\n\n"
        'Respond with ONLY a JSON object: {"score": <int 1-5>, "reason": "<one sentence>"}'
    )

    @property
    def name(self) -> str:
        return "output_quality"

    @property
    def requires_llm(self) -> bool:
        return True

    async def compute(self, actual_trace, golden_case, judge=None):
        # Extract golden final content
        golden_content = ""
        gt = golden_case.golden_trajectory
        if gt and gt.get("turns"):
            last_turn = gt["turns"][-1]
            golden_content = last_turn.get("assistant_final", {}).get("content", "")
            if not golden_content:
                golden_content = last_turn.get("assistant", {}).get("content", "")

        # Extract actual final content
        actual_content = ""
        for e in reversed(actual_trace):
            if e.get("type") == "model_call_end":
                content = e.get("data", {}).get("content", "")
                if content:
                    actual_content = content
                    break

        if not golden_content or not actual_content:
            return {"output_quality": 3, "reason": "missing content for comparison"}

        return await self._call_judge(judge, golden_content, actual_content)

    async def _call_judge(self, judge, golden_content, actual_content):
        from openai import OpenAI

        api_key = os.environ.get(judge.api_key_env, "")
        client = OpenAI(api_key=api_key, base_url=judge.api_base)

        prompt = self._PROMPT.format(golden=golden_content, actual=actual_content)
        resp = client.chat.completions.create(
            model=judge.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=judge.temperature,
            max_tokens=judge.max_tokens,
        )
        try:
            result = json.loads(resp.choices[0].message.content)
            return {"output_quality": int(result["score"]), "reason": result["reason"]}
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            return {"output_quality": 3, "reason": "judge response parse error"}

    def compute_sync(self, actual_trace, golden_case, judge=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge))


class TrajectorySimilarityMetric:
    _PROMPT = (
        "You are evaluating whether an AI agent followed the correct procedure.\n\n"
        "Golden trajectory (expected steps):\n{golden}\n\n"
        "Actual trajectory (what the agent did):\n{actual}\n\n"
        "Rate how well the actual trajectory matches the golden trajectory "
        "from 1 (completely wrong steps) to 5 (identical steps/approach). "
        "Consider: correct tools called, correct order, correct reasoning path.\n\n"
        'Respond with ONLY a JSON object: {"score": <int 1-5>, "reason": "<one sentence>"}'
    )

    @property
    def name(self) -> str:
        return "trajectory_similarity"

    @property
    def requires_llm(self) -> bool:
        return True

    async def compute(self, actual_trace, golden_case, judge=None):
        golden_str = json.dumps(
            golden_case.golden_trajectory, ensure_ascii=False, indent=2
        ) if golden_case.golden_trajectory else "{}"

        # Summarize actual trace: tool calls + model outputs
        actual_summary = []
        for e in actual_trace:
            t = e.get("type", "")
            if t == "tool_call_start":
                actual_summary.append(
                    f"[turn {e.get('turn', 0)}] call "
                    f"{e.get('data', {}).get('tool_name', '?')}"
                )
            elif t == "tool_call_end":
                actual_summary.append(
                    f"[turn {e.get('turn', 0)}] result: "
                    f"{'ok' if e.get('data', {}).get('success') else 'fail'}"
                )
            elif t == "model_call_end":
                content = e.get("data", {}).get("content", "")
                if content:
                    actual_summary.append(
                        f"[turn {e.get('turn', 0)}] output: {content[:200]}"
                    )
        actual_str = "\n".join(actual_summary)

        if not actual_str:
            return {"trajectory_similarity": 3, "reason": "empty actual trajectory"}

        return await self._call_judge(judge, golden_str, actual_str)

    async def _call_judge(self, judge, golden_str, actual_str):
        from openai import OpenAI

        api_key = os.environ.get(judge.api_key_env, "")
        client = OpenAI(api_key=api_key, base_url=judge.api_base)

        prompt = self._PROMPT.format(golden=golden_str, actual=actual_str)
        resp = client.chat.completions.create(
            model=judge.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=judge.temperature,
            max_tokens=judge.max_tokens,
        )
        try:
            result = json.loads(resp.choices[0].message.content)
            return {"trajectory_similarity": int(result["score"]),
                    "reason": result["reason"]}
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            return {"trajectory_similarity": 3, "reason": "judge response parse error"}

    def compute_sync(self, actual_trace, golden_case, judge=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge))
