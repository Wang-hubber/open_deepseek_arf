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
    "你是一个精确的评测标注员。你的任务是为 agent 的对话定义\"正确行为\"的"
    "关键词——不是描述实际输出，而是描述**期望输出**。\n\n"
    "核心原则：\n"
    "1. 标注是对\"正确行为\"的定义，不是对\"实际行为\"的描述\n"
    "2. 关键词是**语义锚点**，不是文本碎片——用上位概念而非摘抄原文\n"
    "   ✓ 好：[\"吃\", \"身份确认\"]\n"
    "   ✗ 差：[\"先不盛\", \"随叫随到\"]（换个表达就匹配不上）\n"
    "3. 宁少勿滥：2 个精准关键词 > 5 个模糊关键词\n"
    "4. 只输出一个 JSON 字符串数组，不要其他内容\n\n"
    "反馈驱动标注：\n"
    "- 如果人工反馈评分是 \"like\"：实际行为≈正确行为，从实际输出中提取关键词\n"
    "- 如果人工反馈评分是 \"dislike\"：忽略实际做了什么，根据反馈原因推导**正确做法**\n"
    "- 如果没有反馈：独立判断正确行为\n\n"
    "示例输出格式：[\"关键词1\", \"关键词2\"]\n\n"
    "---\n"
    "用户输入：{input}\n\n"
    "Agent 实际输出：{original_output}\n"
    "Agent 实际调用的工具：{tool_names}\n"
    "{context_summary}"
    "{feedback}"
)

DEFAULT_EXECUTION_PROMPT = (
    "你是一个精确的评测标注员。你的任务是为 agent 的对话定义\"正确行为\"的"
    "**必须工具调用序列**——不是列出实际调用了什么，而是**正确流程必须调用什么**。\n\n"
    "核心原则：\n"
    "1. 标注是\"必须调用的工具\"，不是\"实际调用的工具\"\n"
    "2. 只列出**必需**的工具（缺了它就完成不了任务），不要列出可选的\n"
    "3. 按正确调用顺序排列\n"
    "4. 很多请求根本不需要工具——此时返回空数组 []\n"
    "5. 只输出一个 JSON 字符串数组，不要其他内容\n\n"
    "反馈驱动标注：\n"
    "- 如果人工反馈评分是 \"like\"：实际行为≈正确行为，参考实际工具调用\n"
    "- 如果人工反馈评分是 \"dislike\"：实际行为有误，根据反馈原因推导**正确工具**\n"
    "  （例：反馈说\"不应调用 write_memory\"→ execution=[]）\n"
    "- 如果没有反馈：独立判断正确流程\n\n"
    "示例输出格式：[\"read_file\", \"write_file\"]\n\n"
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
