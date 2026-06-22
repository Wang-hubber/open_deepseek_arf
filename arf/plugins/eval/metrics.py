"""Evaluation metrics — rule-based and LLM-as-judge."""
import asyncio
import json
from typing import Protocol, runtime_checkable

from arf.plugins.eval.exceptions import EvalJudgeError

_PLACEHOLDER_PREFIX = "[待标注]"


def _filter_placeholders(items: list[str]) -> list[str]:
    """Remove annotation placeholder entries so metrics ignore them.

    Annotators can append real keywords/tool names after the placeholder
    without deleting it — only non-placeholder entries are scored.
    """
    return [x for x in items if not x.startswith(_PLACEHOLDER_PREFIX)]


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
        judge_adapter: "ModelAdapter | None" = None,
    ) -> dict[str, float | str]: ...


class SuccessRateMetric:
    @property
    def name(self) -> str:
        return "success_rate"

    @property
    def requires_llm(self) -> bool:
        return False

    async def compute(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        errors = sum(1 for e in actual_trace if e.get("type") == "error")
        return {"success_rate": 0.0 if errors > 0 else 1.0}

    def compute_sync(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge, judge_adapter))


class ToolCallAccuracyMetric:
    """Name-based tool call accuracy: name + params subset matching.

    Matches expected tool entries from expected_execution against actual calls
    by name (not index), so parallel/out-of-order tool calls are handled correctly.
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

    async def compute(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        actual_calls: list[dict] = []
        dep_order_failures = 0
        for e in actual_trace:
            if e.get("type") == "tool_call_start":
                data = e.get("data", {})
                actual_calls.append({
                    "tool_name": data.get("name") or data.get("tool_name", ""),
                    "arguments": self._parse_arguments(data.get("arguments", "{}")),
                    "blocked": False,
                    "success": True,
                    "result": "",
                })

        # Pair tool_call_end events with their start events (same order)
        end_idx = 0
        for e in actual_trace:
            if e.get("type") == "tool_call_end":
                data = e.get("data", {})
                if end_idx < len(actual_calls):
                    actual_calls[end_idx]["blocked"] = data.get("blocked", False)
                    actual_calls[end_idx]["success"] = data.get("success", True)
                    actual_calls[end_idx]["result"] = data.get("result", "")
                    actual_calls[end_idx]["error"] = data.get("error", "")
                end_idx += 1
                if not data.get("success") and data.get("error"):
                    if self._is_dependency_error(data["error"]):
                        dep_order_failures += 1

        # expected_execution is list[str] — tool names to match by name
        expected_names = _filter_placeholders(golden_case.expected_execution)

        result: dict = {"tool_call_accuracy": 1.0}
        if expected_names:
            total = max(len(expected_names), len(actual_calls) or 1)
            matches = 0
            for exp_name in expected_names:
                if any(act["tool_name"] == exp_name for act in actual_calls):
                    matches += 1
            result["tool_call_accuracy"] = matches / total
        if dep_order_failures > 0:
            result["dependency_order_failures"] = dep_order_failures
        return result

    def _is_dependency_error(self, error_msg: str) -> bool:
        lower = error_msg.lower()
        return any(p in lower for p in self._DEPENDENCY_PATTERNS)

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

    def compute_sync(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge, judge_adapter))


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

    async def compute(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        exec_list = golden_case.expected_execution
        if not exec_list or isinstance(exec_list[0], str):
            return {"tool_call_result_llm": 1.0}
        expected_with_results = [
            e for e in exec_list
            if isinstance(e, dict) and e.get("type") == "tool" and e.get("result")
        ]
        if not expected_with_results:
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
                    judge, judge_adapter, exp_name, user_input, exp["result"], act["result"],
                )
                if result.get("match"):
                    matches += 1
                    break  # found a match, move to next expected

        return {"tool_call_result_llm": matches / total if total > 0 else 1.0}

    async def _call_judge(self, judge, judge_adapter, tool_name, user_input,
                            expected_result, actual_result):
        prompt = self._prompt.format(
            user_input=user_input[:500],
            tool_name=tool_name,
            expected=expected_result[:1500],
            actual=actual_result[:1500],
        )
        messages = []
        if judge and judge.system_prompt:
            messages.append({"role": "system", "content": judge.system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await judge_adapter.chat_complete(messages=messages)
            content = response.content or ""
            result = json.loads(content)
            return {"match": bool(result.get("match", False)),
                    "reason": result.get("reason", "")}
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            return {"match": False, "reason": "judge response parse error"}
        except Exception as e:
            raise EvalJudgeError(
                f"Judge API call failed for {self.name}: {e}"
            ) from e

    def compute_sync(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge, judge_adapter))


class TurnEfficiencyMetric:
    @property
    def name(self) -> str:
        return "turn_efficiency"

    @property
    def requires_llm(self) -> bool:
        return False

    async def compute(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        turn_set = {e.get("turn") or 0 for e in actual_trace if (e.get("turn") or 0) > 0}
        actual_turns = len(turn_set)
        if golden_case.max_turns:
            return {"turn_efficiency": min(1.0, golden_case.max_turns / max(actual_turns, 1))}
        return {"turn_efficiency": 1.0}

    def compute_sync(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge, judge_adapter))


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

    _PROMPT_FREE = (
        "You are evaluating the quality of an AI agent's final answer. "
        "You do NOT have a golden reference — evaluate the answer on its own merits.\n\n"
        "Agent system prompt: {system_prompt}\n"
        "Available tools and descriptions: {tools}\n\n"
        "User's original request: {user_input}\n\n"
        "Agent's final answer:\n<<<ANSWER>>>\n{actual}\n<<<END>>>\n\n"
        "Rate the answer on a 1–5 scale. The answer should be judged against "
        "what a reasonable agent with these instructions and tools SHOULD produce.\n\n"
        "Scoring rubric:\n"
        "5 — Excellent: Fully addresses the user's request. Correct, complete, "
        "well-structured, and aligned with the system prompt's constraints. "
        "If the system prompt limits behavior (e.g., brevity), compliance "
        "with that constraint IS quality.\n"
        "4 — Good: Addresses the core request but has minor flaws — slightly "
        "incomplete, mildly verbose, or missing a secondary detail.\n"
        "3 — Adequate: Partially addresses the request. Notable omissions or "
        "unclear phrasing. A user would need follow-up questions.\n"
        "2 — Poor: Mostly does not address the request. Key information missing "
        "or wrong. A user would reject this answer.\n"
        "1 — Wrong/Harmful: Completely irrelevant, contradicts the user's "
        "request, or violates explicit system prompt prohibitions.\n\n"
        "Critical rules:\n"
        "- If the system prompt explicitly restricts behavior (format, length, "
        "content), compliance with those restrictions is REQUIRED for a high "
        "score. A brief answer that follows instructions is NOT a flaw — the "
        "constraint IS the expected behavior.\n"
        "- If system prompt or tools fields are empty, you are missing key "
        "context — note this in your reason and judge conservatively.\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no extra text):\n"
        '{{"score": <int 1-5>, "reason": "<2-3 sentences>"}}'
    )

    def __init__(self, prompt: str | None = None,
                 prompt_free: str | None = None,
                 system_prompt: str = "", tools: str = ""):
        self._prompt = prompt if prompt else self._PROMPT
        self._prompt_free = prompt_free if prompt_free else self._PROMPT_FREE
        self._system_prompt = system_prompt
        self._tools = tools
        self._trace_dir = "./data"
        self._trace_snapshot_path: str | None = None

    def set_trace_dir(self, trace_dir: str) -> None:
        self._trace_dir = trace_dir

    def set_trace_snapshot_path(self, path: str | None) -> None:
        self._trace_snapshot_path = path

    @property
    def name(self) -> str:
        return "output_quality"

    @property
    def requires_llm(self) -> bool:
        return True

    async def compute(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        # Extract actual final content
        actual_content = ""
        for e in reversed(actual_trace):
            if e.get("type") == "model_call_end":
                content = e.get("data", {}).get("content", "")
                if content:
                    actual_content = content
                    break
        if not actual_content:
            return {"output_quality": None, "reason": "missing actual content"}

        user_input = golden_case.input or ""

        # Reference mode: trace snapshot first, session trace as fallback
        golden_content = self._load_golden_final_output(golden_case.session_id)
        if golden_content:
            return await self._call_judge(
                judge, judge_adapter, user_input, golden_content, actual_content,
            )

        # No-reference mode: evaluate on its own merits
        return await self._call_judge_free(
            judge, judge_adapter, user_input, actual_content,
        )

    def _resolve_trace_path(self, session_id: str | None) -> Path | None:
        """Resolve trace path: snapshot first, then session-scoped trace fallback."""
        from pathlib import Path
        if self._trace_snapshot_path:
            p = Path(self._trace_snapshot_path)
            if p.exists():
                return p
        if session_id:
            p = Path(self._trace_dir) / session_id / "traces" / f"{session_id}.jsonl"
            if p.exists():
                return p
        return None

    def _load_golden_final_output(self, session_id: str | None) -> str | None:
        """Load the final model output from trace snapshot or session trace file."""
        trace_file = self._resolve_trace_path(session_id)
        if trace_file is None:
            return None
        content = None
        with open(trace_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") == "model_call_end":
                    c = e.get("data", {}).get("content", "")
                    if c:
                        content = c
        return content

    async def _call_judge(self, judge, judge_adapter, user_input, golden_content, actual_content):
        prompt = self._prompt.format(
            user_input=user_input[:500],
            golden=golden_content[:2000],
            actual=actual_content[:2000],
        )
        messages = []
        if judge and judge.system_prompt:
            messages.append({"role": "system", "content": judge.system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await judge_adapter.chat_complete(messages=messages)
            content = response.content or ""
            result = json.loads(content)
            return {"output_quality": int(result["score"]), "reason": result["reason"]}
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            return {"output_quality": None, "reason": "judge response parse error"}
        except Exception as e:
            raise EvalJudgeError(
                f"Judge API call failed for {self.name}: {e}"
            ) from e

    async def _call_judge_free(self, judge, judge_adapter, user_input, actual_content):
        prompt = self._prompt_free.format(
            system_prompt=self._system_prompt[:2000],
            tools=self._tools[:2000],
            user_input=user_input[:500],
            actual=actual_content[:2000],
        )
        messages = []
        if judge and judge.system_prompt:
            messages.append({"role": "system", "content": judge.system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await judge_adapter.chat_complete(messages=messages)
            content = response.content or ""
            result = json.loads(content)
            return {"output_quality": int(result["score"]), "reason": result["reason"]}
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            return {"output_quality": None, "reason": "judge response parse error"}
        except Exception as e:
            raise EvalJudgeError(
                f"Judge API call failed for {self.name}: {e}"
            ) from e

    def compute_sync(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge, judge_adapter))


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

    _PROMPT_FREE = (
        "You are evaluating whether an AI agent followed a reasonable procedure "
        "to fulfill the user's request. You do NOT have a golden trajectory — "
        "evaluate the actual steps on their own merits.\n\n"
        "Agent system prompt: {system_prompt}\n"
        "Available tools and descriptions: {tools}\n\n"
        "User's original request: {user_input}\n\n"
        "Agent's actual trajectory:\n<<<TRAJECTORY>>>\n{actual}\n<<<END>>>\n\n"
        "Trajectory format: each entry shows [turn N] with tool calls "
        "(call <name>), results (result: ok/fail), and outputs "
        "(output: <text>).\n\n"
        "Rate the trajectory on a 1–5 scale:\n"
        "5 — Excellent: Efficient, logical sequence. Uses the most appropriate "
        "tools from the available set. No unnecessary steps. If the system "
        "prompt constrains behavior, follows it precisely.\n"
        "4 — Good: Reasonable approach. Minor inefficiency or a slightly "
        "suboptimal tool choice, but still achieves the goal well.\n"
        "3 — Adequate: Gets the job done but with unnecessary detours, repeated "
        "calls, or clearly suboptimal tools given the available set.\n"
        "2 — Poor: Significant issues — wrong tools, confused backtracking, "
        "or failing to make meaningful progress toward the goal.\n"
        "1 — Wrong: Inappropriate approach. Tool choices are irrelevant to the "
        "request. The agent seems to be guessing or ignoring the user.\n\n"
        "Weighting guidelines:\n"
        "- Tool choice should be evaluated against the AVAILABLE tools listed "
        "above, not against an ideal unlimited tool set. If the right tool "
        "doesn't exist, judge whether the agent made the best of what it had.\n"
        "- System prompt constraints are EXPECTED behavior. If told to never "
        "use a certain tool, using it is a flaw regardless of effectiveness.\n"
        "- Extra benign steps that show exploration but don't derail the task "
        "should not significantly lower the score.\n"
        "- If system prompt or tools fields are empty, you are missing key "
        "context — note this in your reason and judge conservatively.\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no extra text):\n"
        '{{"score": <int 1-5>, "reason": "<2-3 sentences>"}}'
    )

    def __init__(self, prompt: str | None = None,
                 prompt_free: str | None = None,
                 system_prompt: str = "", tools: str = ""):
        self._prompt = prompt if prompt else self._PROMPT
        self._prompt_free = prompt_free if prompt_free else self._PROMPT_FREE
        self._system_prompt = system_prompt
        self._tools = tools
        self._trace_dir = "./data"
        self._trace_snapshot_path: str | None = None

    def set_trace_dir(self, trace_dir: str) -> None:
        self._trace_dir = trace_dir

    def set_trace_snapshot_path(self, path: str | None) -> None:
        self._trace_snapshot_path = path

    @property
    def name(self) -> str:
        return "trajectory_similarity"

    @property
    def requires_llm(self) -> bool:
        return True

    async def compute(self, actual_trace, golden_case, judge=None, judge_adapter=None):
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
            return {"trajectory_similarity": None, "reason": "empty actual trajectory"}

        user_input = golden_case.input or ""

        # Reference mode: trace snapshot first, session trace as fallback
        golden_str = self._load_golden_trajectory(golden_case.session_id)
        if golden_str:
            return await self._call_judge(
                judge, judge_adapter, user_input, golden_str, actual_str,
            )

        # No-reference mode
        return await self._call_judge_free(
            judge, judge_adapter, user_input, actual_str,
        )

    def _load_golden_trajectory(self, session_id: str | None) -> str | None:
        from pathlib import Path
        # Snapshot first, session trace as fallback
        trace_file = None
        if self._trace_snapshot_path:
            p = Path(self._trace_snapshot_path)
            if p.exists():
                trace_file = p
        if trace_file is None and session_id:
            p = Path(self._trace_dir) / session_id / "traces" / f"{session_id}.jsonl"
            if p.exists():
                trace_file = p
        if trace_file is None:
            return None
        summary = []
        with open(trace_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = e.get("type", "")
                turn = e.get("turn") or 0
                if t == "tool_call_start":
                    summary.append(
                        f"[turn {turn}] call {e.get('data', {}).get('tool_name', '?')}"
                    )
                elif t == "tool_call_end":
                    summary.append(
                        f"[turn {turn}] result: {'ok' if e.get('data', {}).get('success') else 'fail'}"
                    )
                elif t == "model_call_end":
                    content = e.get("data", {}).get("content", "")
                    if content:
                        summary.append(f"[turn {turn}] output: {content[:200]}")
        return "\n".join(summary) if summary else None

    async def _call_judge(self, judge, judge_adapter, user_input, golden_str, actual_str):
        prompt = self._prompt.format(
            user_input=user_input[:500],
            golden=golden_str[:3000],
            actual=actual_str[:3000],
        )
        messages = []
        if judge and judge.system_prompt:
            messages.append({"role": "system", "content": judge.system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await judge_adapter.chat_complete(messages=messages)
            content = response.content or ""
            result = json.loads(content)
            return {"trajectory_similarity": int(result["score"]),
                    "reason": result["reason"]}
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            return {"trajectory_similarity": None, "reason": "judge response parse error"}
        except Exception as e:
            raise EvalJudgeError(
                f"Judge API call failed for {self.name}: {e}"
            ) from e

    async def _call_judge_free(self, judge, judge_adapter, user_input, actual_str):
        prompt = self._prompt_free.format(
            system_prompt=self._system_prompt[:2000],
            tools=self._tools[:2000],
            user_input=user_input[:500],
            actual=actual_str[:3000],
        )
        messages = []
        if judge and judge.system_prompt:
            messages.append({"role": "system", "content": judge.system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await judge_adapter.chat_complete(messages=messages)
            content = response.content or ""
            result = json.loads(content)
            return {"trajectory_similarity": int(result["score"]),
                    "reason": result["reason"]}
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            return {"trajectory_similarity": None, "reason": "judge response parse error"}
        except Exception as e:
            raise EvalJudgeError(
                f"Judge API call failed for {self.name}: {e}"
            ) from e

    def compute_sync(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        return asyncio.run(self.compute(actual_trace, golden_case, judge, judge_adapter))


class OutputContainsMetric:
    """Check that actual output contains all expected keywords (rule-based)."""

    @property
    def name(self) -> str:
        return "output_contains"

    @property
    def requires_llm(self) -> bool:
        return False

    async def compute(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        expected = _filter_placeholders(golden_case.expected_output_contains)
        if not expected:
            return {"output_contains": 1.0}

        actual_content = ""
        for e in reversed(actual_trace):
            if e.get("type") == "model_call_end":
                content = e.get("data", {}).get("content", "")
                if content:
                    actual_content = content
                    break

        if not actual_content:
            return {"output_contains": 0.0}

        matches = sum(1 for kw in expected if kw in actual_content)
        return {"output_contains": matches / len(expected)}

    def compute_sync(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        import asyncio
        return asyncio.run(self.compute(actual_trace, golden_case, judge, judge_adapter))


class ExecutionAccuracyMetric:
    """Rule-based: compares expected_execution (list of tool names) against
    actual tool calls by name. Returns 1.0 when empty."""

    @property
    def name(self) -> str:
        return "execution_accuracy"

    @property
    def requires_llm(self) -> bool:
        return False

    async def compute(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        expected: list[str] = _filter_placeholders(golden_case.expected_execution)
        if not expected:
            return {"execution_accuracy": 1.0}

        actual_names: set[str] = set()
        for e in actual_trace:
            if e.get("type") == "tool_call_start":
                data = e.get("data", {})
                name = data.get("name") or data.get("tool_name", "")
                if name:
                    actual_names.add(name)

        matches = sum(1 for exp_name in expected if exp_name in actual_names)
        return {"execution_accuracy": matches / len(expected)}

    def compute_sync(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        import asyncio
        return asyncio.run(self.compute(actual_trace, golden_case, judge, judge_adapter))


class ReasoningSimilarityMetric:
    """LLM-judged: compares expected_reasoning against actual model reasoning
    traces. Degradable — N/A when no judge or expected_reasoning empty."""

    _PROMPT = (
        "You are evaluating whether an AI agent's reasoning process matches "
        "the expected reasoning steps for a user request.\n\n"
        "User's original request: {user_input}\n\n"
        "Expected reasoning steps:\n<<<EXPECTED>>>\n{expected}\n<<<END>>>\n\n"
        "Actual reasoning trace:\n<<<ACTUAL>>>\n{actual}\n<<<END>>>\n\n"
        "Rate the actual reasoning against the expected on a 1-5 scale:\n"
        "5 — Excellent: All expected reasoning steps covered, same logical "
        "flow, key insights present. Minor wording differences acceptable.\n"
        "4 — Good: Core reasoning present but one or two steps missing or "
        "slightly out of order.\n"
        "3 — Adequate: Some expected steps present, but notable gaps in logic.\n"
        "2 — Poor: Reasoning mostly misaligned. Key insights missing.\n"
        "1 — Wrong: Completely different reasoning that misses the point.\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no extra text):\n"
        '{{"score": <int 1-5>, "reason": "<2-3 sentences>"}}'
    )

    def __init__(self, prompt: str | None = None):
        self._prompt = prompt if prompt else self._PROMPT

    @property
    def name(self) -> str:
        return "reasoning_similarity"

    @property
    def requires_llm(self) -> bool:
        return True

    async def compute(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        expected = getattr(golden_case, "expected_reasoning", [])
        if not expected:
            return {"reasoning_similarity": None, "reason": "no expected_reasoning"}

        if judge_adapter is None:
            return {"reasoning_similarity": None, "reason": "no judge model"}

        actual_reasoning_parts: list[str] = []
        for e in actual_trace:
            if e.get("type") == "model_call_end":
                reasoning = e.get("data", {}).get("reasoning", "")
                if reasoning:
                    actual_reasoning_parts.append(reasoning)

        actual_str = "\n".join(actual_reasoning_parts) if actual_reasoning_parts else "(no reasoning recorded)"
        expected_str = "\n".join(f"{i+1}. {s}" for i, s in enumerate(expected))

        prompt = self._prompt.format(
            user_input=golden_case.input[:500],
            expected=expected_str[:2000],
            actual=actual_str[:2000],
        )
        messages = []
        if judge and judge.system_prompt:
            messages.append({"role": "system", "content": judge.system_prompt})
        messages.append({"role": "user", "content": prompt})

        import json
        try:
            response = await judge_adapter.chat_complete(messages=messages)
            content = response.content or ""
            result = json.loads(content)
            return {"reasoning_similarity": int(result["score"]),
                    "reason": result.get("reason", "")}
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            return {"reasoning_similarity": None, "reason": "judge response parse error"}
        except Exception as e:
            from arf.plugins.eval.exceptions import EvalJudgeError
            raise EvalJudgeError(
                f"Judge API call failed for {self.name}: {e}"
            ) from e

    def compute_sync(self, actual_trace, golden_case, judge=None, judge_adapter=None):
        import asyncio
        return asyncio.run(self.compute(actual_trace, golden_case, judge, judge_adapter))
