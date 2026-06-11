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
    """Indexed tool call accuracy: name + params subset matching.

    When expected_tool_calls is set, compares tool calls by index
    with params subset matching. Falls back to expected_tools (name-only)
    when expected_tool_calls is None.
    """

    @property
    def name(self) -> str:
        return "tool_call_accuracy"

    @property
    def requires_llm(self) -> bool:
        return False

    async def compute(self, actual_trace, golden_case, judge=None):
        # Collect actual tool calls (tool_call_start events)
        actual_calls: list[dict] = []
        for e in actual_trace:
            if e.get("type") == "tool_call_start":
                data = e.get("data", {})
                actual_calls.append({
                    "tool_name": data.get("tool_name", ""),
                    "arguments": self._parse_arguments(data.get("arguments", "{}")),
                })

        if golden_case.expected_tool_calls:
            return self._compute_with_params(golden_case.expected_tool_calls, actual_calls)
        elif golden_case.expected_tools:
            return self._compute_name_only(golden_case.expected_tools, actual_calls)
        return {"tool_call_accuracy": 1.0}

    def _compute_with_params(self, expected_calls, actual_calls):
        total = max(len(expected_calls), len(actual_calls) or 1)
        matches = 0
        for i, exp in enumerate(expected_calls):
            if i >= len(actual_calls):
                break
            act = actual_calls[i]
            if exp.get("name", "") != act["tool_name"]:
                continue
            if not self._params_subset(
                exp.get("params", {}),
                act["arguments"],
            ):
                continue
            matches += 1
        return {"tool_call_accuracy": matches / total}

    def _compute_name_only(self, expected_names, actual_calls):
        actual_names = [a["tool_name"] for a in actual_calls]
        if not actual_names:
            return {"tool_call_accuracy": 0.0}
        matches = sum(
            1 for a, e in zip(actual_names, expected_names) if a == e
        )
        return {"tool_call_accuracy": matches / len(expected_names)}

    @staticmethod
    def _params_subset(expected: dict, actual: dict) -> bool:
        """True if actual contains all k-v pairs from expected (substring match for values)."""
        for k, v in expected.items():
            av = actual.get(k)
            if av is None:
                return False
            if isinstance(v, str) and v not in str(av):
                return False
            if not isinstance(v, str) and av != v:
                return False
        return True

    @staticmethod
    def _parse_arguments(arguments):
        """Parse tool_call_start arguments, which may be a JSON string or dict."""
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                return {"_raw": arguments}
        return {}

    def compute_sync(self, actual_trace, golden_case, judge=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge))


class ToolCallResultLLMMetric:
    """LLM-judged semantic equivalence of tool call results.

    Compares expected result strings against actual tool results,
    using an LLM to determine semantic equivalence. Used alongside
    ToolCallAccuracyMetric (which handles name + params matching).
    """

    _PROMPT = (
        "You are evaluating whether a tool call result matches expectations.\n\n"
        "Expected result: {expected}\n"
        "Actual result: {actual}\n\n"
        "Are these semantically equivalent? Consider: does the actual "
        "result convey the same information and outcome as the expected "
        "result, even if worded differently?\n\n"
        'Respond with ONLY a JSON object: '
        '{"match": <true or false>, "reason": "<one sentence>"}'
    )

    @property
    def name(self) -> str:
        return "tool_call_result_llm"

    @property
    def requires_llm(self) -> bool:
        return True

    async def compute(self, actual_trace, golden_case, judge=None):
        if not golden_case.expected_tool_calls:
            return {"tool_call_result_llm": 1.0}

        # Collect actual tool results (tool_call_end events)
        actual_results: list[dict] = []
        for e in actual_trace:
            if e.get("type") == "tool_call_end":
                data = e.get("data", {})
                actual_results.append({
                    "tool_name": data.get("tool_name", ""),
                    "result": data.get("result", ""),
                    "success": data.get("success", False),
                })

        expected_with_results = [
            e for e in golden_case.expected_tool_calls if e.get("result")
        ]
        if not expected_with_results:
            return {"tool_call_result_llm": 1.0}

        if not judge:
            return {"tool_call_result_llm": 0.0, "reason": "no judge configured"}

        matches = 0
        total = len(expected_with_results)
        for i, exp in enumerate(expected_with_results):
            if i >= len(actual_results):
                break
            act = actual_results[i]
            if not act["result"]:
                continue
            result = await self._call_judge(
                judge, exp["result"], act["result"]
            )
            if result.get("match"):
                matches += 1

        return {"tool_call_result_llm": matches / total if total > 0 else 1.0}

    async def _call_judge(self, judge, expected_result, actual_result):
        from openai import OpenAI

        api_key = os.environ.get(judge.api_key_env, "")
        client = OpenAI(api_key=api_key or "placeholder", base_url=judge.api_base)

        prompt = self._PROMPT.format(
            expected=expected_result[:1000],
            actual=actual_result[:1000],
        )
        resp = client.chat.completions.create(
            model=judge.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=judge.temperature,
            max_tokens=judge.max_tokens,
        )
        try:
            result = json.loads(resp.choices[0].message.content)
            return {"match": bool(result.get("match", False)),
                    "reason": result.get("reason", "")}
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            return {"match": False, "reason": "judge response parse error"}

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
