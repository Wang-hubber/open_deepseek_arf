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
    """Name-based tool call accuracy: name + params subset matching.

    Matches expected tool calls against actual calls by name (not index),
    so parallel/out-of-order tool calls are handled correctly.
    Falls back to expected_tools (name-only) when expected_tool_calls is None.
    Also counts dependency-order failures from tool_call_end error messages.
    """

    _DEPENDENCY_PATTERNS = (
        "depends_on", "blocked", "not ready", "not complete",
        "dependency", "must complete", "waiting for", "prerequisite",
    )

    @property
    def name(self) -> str:
        return "tool_call_accuracy"

    @property
    def requires_llm(self) -> bool:
        return False

    async def compute(self, actual_trace, golden_case, judge=None):
        actual_calls: list[dict] = []
        dep_order_failures = 0
        for e in actual_trace:
            if e.get("type") == "tool_call_start":
                data = e.get("data", {})
                actual_calls.append({
                    "tool_name": data.get("tool_name", ""),
                    "arguments": self._parse_arguments(data.get("arguments", "{}")),
                })
            elif e.get("type") == "tool_call_end":
                data = e.get("data", {})
                if not data.get("success") and data.get("error"):
                    if self._is_dependency_error(data["error"]):
                        dep_order_failures += 1

        result: dict = {"tool_call_accuracy": 1.0}
        if golden_case.expected_tool_calls:
            result.update(self._compute_with_params(golden_case.expected_tool_calls, actual_calls))
        elif golden_case.expected_tools:
            result.update(self._compute_name_only(golden_case.expected_tools, actual_calls))
        if dep_order_failures > 0:
            result["dependency_order_failures"] = dep_order_failures
        return result

    def _is_dependency_error(self, error_msg: str) -> bool:
        lower = error_msg.lower()
        return any(p in lower for p in self._DEPENDENCY_PATTERNS)

    def _compute_with_params(self, expected_calls, actual_calls):
        total = max(len(expected_calls), len(actual_calls) or 1)
        matches = 0
        for exp in expected_calls:
            exp_name = exp.get("name", "")
            exp_params = exp.get("params", {})
            for act in actual_calls:
                if act["tool_name"] != exp_name:
                    continue
                if not self._params_subset(exp_params, act["arguments"]):
                    continue
                matches += 1
                break  # found a match, move to next expected
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

    Matches expected tool calls against actual results by name (not index),
    then uses an LLM to judge semantic equivalence of result strings.
    Used alongside ToolCallAccuracyMetric (which handles name + params matching).
    """

    _PROMPT = (
        "You are comparing the output of a tool call against the expected reference output.\n\n"
        "User's original request: {user_input}\n\n"
        "Tool: {tool_name}\n"
        "Expected output:\n<<<EXPECTED>>>\n{expected}\n<<<END>>>\n\n"
        "Actual output:\n<<<ACTUAL>>>\n{actual}\n<<<END>>>\n\n"
        "Determine whether the actual output is **semantically equivalent** to the "
        "expected output. Semantic equivalence means: both outputs convey the same "
        "functional information — they would lead the agent to the same next decision "
        "or action, even if wording, formatting, or verbosity differ.\n\n"
        "Judgment criteria:\n"
        "- true : Core information is preserved. Minor formatting differences, extra "
        "details that do not change meaning, or different phrasing of the same facts "
        "are all acceptable.\n"
        "- false: Critical information is missing, contradictory facts are present, "
        "or the actual output is an error/failure while the expected was successful.\n\n"
        "Edge cases:\n"
        "- If either output appears truncated, judge based on the available content.\n"
        "- If the actual output is an error/failure but the expected was successful, "
        "this is unequivocally false.\n"
        "- If both are errors/failures of the same type, they are equivalent (true).\n"
        "- Numeric values: treat ±5% deviation as equivalent unless precision is "
        "clearly critical to the user's request.\n"
        "- If the actual output contains the expected output verbatim plus extra "
        "surrounding context, it is still equivalent.\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no extra text):\n"
        '{{"match": <bool>, "reason": "<2-3 sentences explaining the key '
        'similarities or differences that determined your judgment>"}}'
    )

    def __init__(self, prompt: str | None = None):
        self._prompt = prompt if prompt else self._PROMPT

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

        user_input = golden_case.input or ""
        matches = 0
        total = len(expected_with_results)
        for exp in expected_with_results:
            exp_name = exp.get("name", "")
            for act in actual_results:
                if act["tool_name"] != exp_name:
                    continue
                if not act["result"]:
                    continue
                result = await self._call_judge(
                    judge, exp_name, user_input, exp["result"], act["result"],
                )
                if result.get("match"):
                    matches += 1
                    break  # found a match, move to next expected

        return {"tool_call_result_llm": matches / total if total > 0 else 1.0}

    async def _call_judge(self, judge, tool_name, user_input,
                            expected_result, actual_result):
        from openai import OpenAI

        api_key = os.environ.get(judge.api_key_env, "")
        client = OpenAI(api_key=api_key or "placeholder", base_url=judge.api_base)

        prompt = self._prompt.format(
            user_input=user_input[:500],
            tool_name=tool_name,
            expected=expected_result[:1500],
            actual=actual_result[:1500],
        )
        messages = []
        if judge.system_prompt:
            messages.append({"role": "system", "content": judge.system_prompt})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=judge.model,
            messages=messages,
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
        "You are evaluating the quality of an AI agent's final answer against "
        "a golden reference answer.\n\n"
        "User's original request: {user_input}\n\n"
        "Golden reference answer:\n<<<GOLDEN>>>\n{golden}\n<<<END>>>\n\n"
        "Actual answer:\n<<<ACTUAL>>>\n{actual}\n<<<END>>>\n\n"
        "Rate the actual answer on a 1–5 scale. The goal is NOT verbatim matching "
        "— a differently worded answer that serves the user equally well should "
        "score high. Conversely, a verbatim copy that misses the user's intent "
        "should score low.\n\n"
        "Scoring rubric (anchor on the behavioral description, not the label):\n"
        "5 — Excellent: Effectively addresses the user's request. Factually correct, "
        "complete, clear, and well-structured. A user would be fully satisfied.\n"
        "4 — Good: Addresses the request but has minor flaws — slightly incomplete, "
        "mildly verbose, or missing a secondary detail. Still clearly useful.\n"
        "3 — Adequate: Partially addresses the request. Contains some correct "
        "information but has notable omissions, unclear phrasing, or minor "
        "factual issues. A user would need to ask follow-up questions.\n"
        "2 — Poor: Mostly incorrect, irrelevant, or so incomplete that the core "
        "request is not meaningfully answered. A user would reject this answer.\n"
        "1 — Wrong/Harmful: Completely incorrect, contradicts the golden answer "
        "on key facts, or provides dangerously misleading information.\n\n"
        "Important distinctions:\n"
        "- Factual errors are worse than stylistic issues (verbose but correct "
        "should not drop below 4).\n"
        "- An answer that is correct but less detailed than the golden answer "
        "may still score 4 if the essentials are there.\n"
        "- An answer that adds genuinely useful context beyond the golden "
        "answer should NOT be penalized — it can still score 5.\n"
        "- If both answers are functionally identical but phrased differently, "
        "score 5.\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no extra text):\n"
        '{{"score": <int 1-5>, "reason": "<2-3 sentences justifying the score '
        'with specific reference to what the actual answer did well or poorly>"}}'
    )

    def __init__(self, prompt: str | None = None):
        self._prompt = prompt if prompt else self._PROMPT

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

        user_input = golden_case.input or ""
        return await self._call_judge(judge, user_input, golden_content, actual_content)

    async def _call_judge(self, judge, user_input, golden_content, actual_content):
        from openai import OpenAI

        api_key = os.environ.get(judge.api_key_env, "")
        client = OpenAI(api_key=api_key, base_url=judge.api_base)

        prompt = self._prompt.format(
            user_input=user_input[:500],
            golden=golden_content[:2000],
            actual=actual_content[:2000],
        )
        messages = []
        if judge.system_prompt:
            messages.append({"role": "system", "content": judge.system_prompt})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=judge.model,
            messages=messages,
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
        "You are evaluating whether an AI agent followed a correct procedure "
        "to fulfill the user's request.\n\n"
        "User's original request: {user_input}\n\n"
        "Golden trajectory (reference procedure):\n<<<GOLDEN>>>\n{golden}\n<<<END>>>\n\n"
        "Actual trajectory (what the agent did):\n<<<ACTUAL>>>\n{actual}\n<<<END>>>\n\n"
        "The golden trajectory is ONE correct way to solve the problem, not the "
        "ONLY correct way. The agent may take a different but equally valid path.\n\n"
        "Trajectory format: each entry represents a turn with [turn N] prefixes. "
        "Entries show tool calls (call <name>), tool results (result: ok/fail), "
        "and model outputs (output: <text>).\n\n"
        "Rate the actual trajectory on a 1–5 scale:\n"
        "5 — Excellent match: Uses the same key tools/steps in a logical order "
        "to achieve the goal. Minor variations (extra benign calls, slightly "
        "different order for independent steps) are acceptable.\n"
        "4 — Good match: Core approach is correct. Differs in some tool choices "
        "or order, but still reaches the goal competently.\n"
        "3 — Partial match: Some correct steps but missing critical tool calls, "
        "wrong order for dependent steps, or includes clearly unnecessary detours.\n"
        "2 — Poor match: The approach is largely wrong. Key tools are missing or "
        "misused. The agent seems to be guessing or going in circles.\n"
        "1 — No match: Completely wrong approach, irrelevant tool calls, or the "
        "trajectory fails to address the user's request at all.\n\n"
        "Weighting guidelines:\n"
        "- Using the right tools matters more than using them in the exact same "
        "order (unless the order reflects a hard dependency).\n"
        "- Extra benign tool calls that don't affect the outcome should not "
        "significantly lower the score.\n"
        "- Missing a critical tool that is essential to the solution should drop "
        "the score by at least 2 points.\n"
        "- If the actual trajectory achieves the same result through a genuinely "
        "different but equally sound approach, score 5.\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no extra text):\n"
        '{{"score": <int 1-5>, "reason": "<2-3 sentences justifying the score '
        'with specific reference to which steps matched or diverged>"}}'
    )

    def __init__(self, prompt: str | None = None):
        self._prompt = prompt if prompt else self._PROMPT

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

        user_input = golden_case.input or ""
        return await self._call_judge(judge, user_input, golden_str, actual_str)

    async def _call_judge(self, judge, user_input, golden_str, actual_str):
        from openai import OpenAI

        api_key = os.environ.get(judge.api_key_env, "")
        client = OpenAI(api_key=api_key, base_url=judge.api_base)

        prompt = self._prompt.format(
            user_input=user_input[:500],
            golden=golden_str[:3000],
            actual=actual_str[:3000],
        )
        messages = []
        if judge.system_prompt:
            messages.append({"role": "system", "content": judge.system_prompt})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=judge.model,
            messages=messages,
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
