"""AutoAnnotator — LLM-driven benchmark annotation.

Fills ``expected_output_contains`` and ``expected_execution`` for each
case in an EvalBenchmark so the user doesn't have to edit JSON by hand.
"""
from __future__ import annotations

import json as _json
import logging
from pathlib import Path

from arf.plugins.eval.models import EvalBenchmark, EvalCase

logger = logging.getLogger("arf.eval.annotator")

# ── default prompts ──────────────────────────────────────────────

DEFAULT_OUTPUT_CONTAINS_PROMPT = (
    "你是一个精确的评测标注员。为 agent 的对话提取**期望输出中应出现的词**。\n\n"
    "标注心智模型：\n"
    "1. 先想：如果 agent 正确处理了这个请求，它的回复里会出现哪些词？\n"
    "2. 从你想象的\"正确回复\"中挑出最关键的实义词（名词、动词），不是描述行为类型\n"
    "   关键词应该能被 `in` 运算符直接命中——是你期望在文本里看到的字\n"
    "3. 语义锚点，不摘抄原文：用能概括语义的具体词，不必原样出现在实际输出中\n"
    "4. 宁少勿滥：2 个精准的 > 5 个模糊的\n"
    "5. 只输出一个 JSON 字符串数组，不要其他内容\n\n"
    "反馈驱动：\n"
    "- like → 从实际输出中提取\n"
    "- dislike → 忽略实际输出，根据反馈原因推导正确回复应包含的词\n"
    "- 无反馈 → 独立判断\n\n"
    "输出格式：[\"词1\", \"词2\"]\n\n"
    "---\n"
    "用户输入：{input}\n\n"
    "Agent 实际输出：{original_output}\n"
    "Agent 实际调用的工具：{tool_names}\n"
    "{context_summary}"
    "{feedback}"
)

DEFAULT_EXECUTION_PROMPT = (
    "你是一个精确的评测标注员。为 agent 的请求定义**正确流程必须调用的工具**。\n\n"
    "标注心智模型：\n"
    "1. 先想：正确处理这个请求的流程是什么？哪些工具是**不可跳过**的？\n"
    "2. 只列必需的，不列可选的。很多请求不需要任何工具——此时输出 []\n"
    "3. 标的是\"正确做法\"，不是\"实际做法\"。如果实际行为有误，标注正确的\n"
    "4. 按调用顺序排列\n"
    "5. 只输出一个 JSON 字符串数组，不要其他内容\n\n"
    "反馈驱动：\n"
    "- like → 参考实际工具调用\n"
    "- dislike → 实际行为有误，根据反馈原因推导正确工具\n"
    "- 无反馈 → 独立判断\n\n"
    "输出格式：[\"tool_a\", \"tool_b\"]\n\n"
    "---\n"
    "用户输入：{input}\n\n"
    "Agent 实际输出：{original_output}\n"
    "Agent 实际调用的工具：{tool_names}\n"
    "{context_summary}"
    "{feedback}"
)

# ── helpers ───────────────────────────────────────────────────────

def _build_context_summary(case: EvalCase) -> str:
    """Build a condensed summary of prior conversation from context_messages."""
    if not case.context_messages:
        return ""
    parts: list[str] = ["前序对话摘要："]
    for msg in case.context_messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, dict):
            content = content.get("content", "")
        if not content:
            continue
        label = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(role, role)
        parts.append(f"[{label}] {str(content)[:200]}")
    return "\n".join(parts) if len(parts) > 1 else ""


def _build_feedback_str(feedback: dict | None) -> str:
    """Build a feedback hint string from the case's feedback dict."""
    if not feedback:
        return ""
    rating = feedback.get("rating", "") or feedback.get("comment", "")
    reason = feedback.get("reason", "")
    if not rating and not reason:
        return ""
    lines = ["人工标注反馈："]
    if rating:
        lines.append(f"  评分: {rating}")
    if reason:
        lines.append(f"  原因: {reason}")
    return "\n".join(lines)


class AutoAnnotator:
    """Fill benchmark expected fields via LLM.

    Usage::

        adapter = ModelAdapter({...})
        annotator = AutoAnnotator("benchmarks/my_bm.benchmark.json", adapter)
        await annotator.annotate()
        annotator.save()

    Prompts can be customised via *prompts*::

        annotator = AutoAnnotator(path, adapter, prompts={
            "auto_annotate_output_contains": "Your custom prompt...",
        })
    """

    def __init__(
        self,
        benchmark_path: str,
        model_adapter,
        *,
        prompts: dict[str, str] | None = None,
    ) -> None:
        self._bm_path = Path(benchmark_path)
        self._bm = EvalBenchmark.from_json(str(self._bm_path))
        self._adapter = model_adapter
        self._prompts = dict(prompts or {})

    # ── public API ────────────────────────────────────────────────

    async def annotate_output_contains(self) -> EvalBenchmark:
        """Fill ``expected_output_contains`` for every case."""
        prompt = self._prompts.get(
            "auto_annotate_output_contains", DEFAULT_OUTPUT_CONTAINS_PROMPT,
        )
        for case in self._bm.cases:
            if not self._needs_annotation(case.expected_output_contains):
                continue
            tool_names = [tc["name"] for tc in (case.original_tool_calls or [])]
            user_msg = prompt.format(
                input=case.input,
                original_output=case.original_output,
                tool_names=", ".join(tool_names) if tool_names else "(无工具调用)",
                context_summary=_build_context_summary(case),
                feedback=_build_feedback_str(case.feedback),
            )
            case.expected_output_contains = await self._call_llm(user_msg)
            logger.info("case %s: output_contains → %s", case.id, case.expected_output_contains)
        return self._bm

    async def annotate_execution(self) -> EvalBenchmark:
        """Fill ``expected_execution`` for every case."""
        prompt = self._prompts.get(
            "auto_annotate_execution", DEFAULT_EXECUTION_PROMPT,
        )
        for case in self._bm.cases:
            if not self._needs_annotation(case.expected_execution):
                continue
            tool_names = [tc["name"] for tc in (case.original_tool_calls or [])]
            user_msg = prompt.format(
                input=case.input,
                original_output=case.original_output,
                tool_names=", ".join(tool_names) if tool_names else "(无工具调用)",
                context_summary=_build_context_summary(case),
                feedback=_build_feedback_str(case.feedback),
            )
            case.expected_execution = await self._call_llm(user_msg)
            logger.info("case %s: execution → %s", case.id, case.expected_execution)
        return self._bm

    async def annotate(self) -> EvalBenchmark:
        """Run both output_contains and execution annotation."""
        await self.annotate_output_contains()
        await self.annotate_execution()
        return self._bm

    def save(self, path: str | None = None) -> str:
        """Write the (modified) benchmark back to JSON.  Returns the path written."""
        out = Path(path) if path else self._bm_path
        self._bm.to_json(str(out))
        return str(out)

    @property
    def benchmark(self) -> EvalBenchmark:
        return self._bm

    # ── internals ─────────────────────────────────────────────────

    @staticmethod
    def _needs_annotation(field: list[str]) -> bool:
        """Return True if *field* only contains placeholders (or is empty)."""
        if not field:
            return True
        return all(
            isinstance(v, str) and v.startswith("[待标注]") for v in field
        )

    async def _call_llm(self, user_message: str) -> list[str]:
        """Send a single-turn request and parse the JSON-array response."""
        messages = [{"role": "user", "content": user_message}]
        try:
            resp = await self._adapter.chat_complete(messages, tools=None)
            content = resp.content if hasattr(resp, "content") else str(resp)
        except Exception:
            logger.warning("LLM call failed for annotation", exc_info=True)
            return []

        content = (content or "").strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            parsed = _json.loads(content)
            if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                return [x for x in parsed if not x.startswith("[待标注]")]
        except (_json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse annotation response: %s", content[:200])
        return []
