"""AutoAnnotator — LLM-driven benchmark annotation.

Fills ``expected_output_contains`` and ``expected_execution`` for each
case in an EvalBenchmark.  Builds an internal ARF agent from the appʼs
agent config so every LLM call goes through the full harness pipeline
(trace, hooks, error handling).

Requires explicit ``annotator_model`` in ``plugins_config.eval`` —
raises ``ValueError`` if not configured (defensive programming).
"""
from __future__ import annotations

import json as _json
import logging
import os
import uuid
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


# ── harness builder (shared with judge) ────────────────────────────

def _build_plugin_harness(model_config, data_dir: str = "./data"):
    """Build a minimal ARF harness for plugin-internal LLM calls.

    No plugins, no tools — just the harness pipeline (trace, state,
    session lifecycle) so every call produces a proper session trace.
    """
    from arf.core.model_adapter import ModelAdapter
    from arf.agent.primitive import PrimitiveAgent, ModelResult
    from arf.harness.engine import AgentHarness
    import json as _json

    adapter = ModelAdapter({
        "base_url": model_config.api_base,
        "api_key": os.environ.get(model_config.api_key_env, ""),
        "model_name": model_config.model,
        **model_config.kwargs,
    })

    async def call_model(messages, tools=None):
        msg = await adapter.chat_complete(messages, tools=tools)
        tc_list = []
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    params = _json.loads(tc.function.arguments) if tc.function.arguments else {}
                except _json.JSONDecodeError:
                    params = {}
                tc_list.append({"id": tc.id, "name": tc.function.name, "params": params})
        usage = dict(msg.usage) if hasattr(msg, "usage") and msg.usage else {}
        return ModelResult(
            content=msg.content or "",
            tool_calls=tc_list,
            usage=usage,
            finish_reason=getattr(msg, "finish_reason", "stop"),
        )

    agent = PrimitiveAgent(
        agent_id="eval_plugin",
        model_config={
            "api_base": model_config.api_base,
            "api_key_env": model_config.api_key_env,
            "model_name": model_config.model,
            "context_window": 131072,
        },
        call_model=call_model,
    )

    return AgentHarness(agent, plugins=[], agent_config=None, data_dir=data_dir)


# ── AutoAnnotator ─────────────────────────────────────────────────

class AutoAnnotator:
    """Fill benchmark expected fields via LLM.

    Builds an internal ARF agent from *agent_config*, so every LLM call
    produces a session trace.  Requires explicit ``annotator_model`` in
    ``plugins_config.eval`` — raises ``ValueError`` if missing.

    Usage::

        annotator = AutoAnnotator("bm.benchmark.json", agent_config)
        await annotator.annotate()
        annotator.save()
    """

    def __init__(
        self,
        benchmark_path: str,
        agent_config,  # AgentConfig — used to resolve annotator_model
        *,
        data_dir: str = "./data",
        prompts: dict[str, str] | None = None,
    ) -> None:
        self._bm_path = Path(benchmark_path)
        self._bm = EvalBenchmark.from_json(str(self._bm_path))
        self._prompts = dict(prompts or {})

        model = agent_config.get_plugin_model_config("eval", field="annotator_model")
        if model is None:
            raise ValueError(
                "AutoAnnotator requires plugins_config.eval.annotator_model "
                "to be configured in agent.yaml"
            )
        self._harness = _build_plugin_harness(model, data_dir=data_dir)

    # ── public API ────────────────────────────────────────────────

    async def annotate_output_contains(self) -> EvalBenchmark:
        """Fill ``expected_output_contains`` for every case."""
        prompt_tpl = self._prompts.get(
            "auto_annotate_output_contains", DEFAULT_OUTPUT_CONTAINS_PROMPT,
        )
        for case in self._bm.cases:
            if not self._needs_annotation(case.expected_output_contains):
                continue
            case.expected_output_contains = await self._annotate_one(
                case, prompt_tpl, f"annot_output_{case.id}",
            )
        return self._bm

    async def annotate_execution(self) -> EvalBenchmark:
        """Fill ``expected_execution`` for every case."""
        prompt_tpl = self._prompts.get(
            "auto_annotate_execution", DEFAULT_EXECUTION_PROMPT,
        )
        for case in self._bm.cases:
            if not self._needs_annotation(case.expected_execution):
                continue
            case.expected_execution = await self._annotate_one(
                case, prompt_tpl, f"annot_exec_{case.id}",
            )
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

    async def _annotate_one(self, case: EvalCase, prompt_tpl: str,
                            tag: str) -> list[str]:
        """Run one annotation call through the harness."""
        tool_names = [tc["name"] for tc in (case.original_tool_calls or [])]
        user_msg = prompt_tpl.format(
            input=case.input,
            original_output=case.original_output,
            tool_names=", ".join(tool_names) if tool_names else "(无工具调用)",
            context_summary=_build_context_summary(case),
            feedback=_build_feedback_str(case.feedback),
        )

        sid = f"annotate_{self._bm.name}_{tag}_{uuid.uuid4().hex[:8]}"
        content = ""
        try:
            async for event in self._harness.run(user_msg, session_id=sid):
                if getattr(event, "type", "") == "model_call_end":
                    content = event.data.get("content", "")
        except Exception:
            logger.warning("Annotation call failed for %s", tag, exc_info=True)
            return []

        return self._parse_annotation(content)

    @staticmethod
    def _parse_annotation(raw: str) -> list[str]:
        """Parse the LLM's JSON-array response, stripping markdown fences."""
        content = (raw or "").strip()
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
