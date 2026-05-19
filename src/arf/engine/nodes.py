"""Graph nodes for the ARF agent LangGraph state graph.

Each node is a pure function: (state, config) -> partial state update dict.
Nodes access external dependencies (model, tools, hooks) via config["configurable"].
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from .state import AgentState
from .classifier import classify_request, CLASSIFICATION_TO_MODEL, resolve_model_for_classification

logger = logging.getLogger("arf.graph.nodes")


def _last_user_message_snippet(messages: list[dict], max_len: int = 500) -> str:
    """Extract truncated content from the last user message in the conversation."""
    for m in reversed(messages):
        if m.get("role") == "user" and m.get("content"):
            text = str(m["content"])
            return text[:max_len] + ("..." if len(text) > max_len else "")
    return ""


def _tool_result_snippet(result_str: str, max_len: int = 1000) -> str:
    """Truncate tool result string for trace storage."""
    if not result_str:
        return ""
    return result_str[:max_len] + ("..." if len(result_str) > max_len else "")


# Token thresholds for context compaction.  When total estimated tokens exceed
# COMPACTION_THRESHOLD the compactor summarizes older turns, keeping the most
# recent turns that fit within COMPACTION_KEEP_TOKENS.
COMPACTION_THRESHOLD = 255000   # trigger compaction
COMPACTION_KEEP_TOKENS = 55000  # keep this many tokens of recent turns
_TOOL_OUTPUT_THRESHOLD = 2000   # max chars before progressive disclosure


# Per-character-type token multipliers.  CJK characters are encoded as 1-2
# tokens each by most tokenizers (DeepSeek averages ~1.5, OpenAI cl100k ~2.0).
# 1.8 is a conservative midpoint.  Latin text averages ~3.3-4 chars/token, so
# 0.3 tokens/char slightly overestimates.  Other scripts use 0.5.
_CJK_TOKEN_RATE = 1.8      # tokens per CJK character
_LATIN_TOKEN_RATE = 0.3     # tokens per ASCII/Latin character
_OTHER_TOKEN_RATE = 0.5     # tokens per character for other scripts
_MSG_OVERHEAD = 4           # structural tokens per message (role, keys, etc.)

# Unicode ranges considered "CJK" (wide characters typically encoded as
# multiple tokens).
_CJK_RANGES = (
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x2E80, 0x2EFF),    # CJK Radicals Supplement
    (0x3000, 0x303F),    # CJK Symbols and Punctuation
    (0xFF00, 0xFFEF),    # Halfwidth and Fullwidth Forms
    (0x20000, 0x2A6DF),  # CJK Extension B
    (0x2A700, 0x2B73F),  # CJK Extension C
    (0x2B820, 0x2CEAF),  # CJK Extension E
    (0x2F800, 0x2FA1F),  # CJK Compatibility Ideographs Supplement
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0xAC00, 0xD7AF),    # Hangul Syllables
    (0x1100, 0x11FF),    # Hangul Jamo
)


def _is_cjk(cp: int) -> bool:
    """Check whether a Unicode code point falls in a CJK range."""
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def _char_tokens(cp: int) -> float:
    """Return estimated tokens for a single Unicode character."""
    if cp < 128:
        return _LATIN_TOKEN_RATE
    if _is_cjk(cp):
        return _CJK_TOKEN_RATE
    return _OTHER_TOKEN_RATE


def _text_tokens(text: str) -> float:
    """Estimate tokens for a text string, character by character."""
    return sum(_char_tokens(ord(c)) for c in text)


def _estimate_tokens(messages: list[dict]) -> int:
    """Estimate token count for a list of conversation messages.

    Uses per-character-type multipliers: CJK (1.8), Latin (0.3), other (0.5).
    Accounts for per-message structural overhead (~4 tokens).
    """
    total = 0.0
    for m in messages:
        total += _MSG_OVERHEAD
        content = m.get("content", "") or ""
        reasoning = m.get("reasoning_content", "") or ""
        total += _text_tokens(content) + _text_tokens(reasoning)
        for tc in m.get("tool_calls", []) or []:
            total += _text_tokens(json.dumps(tc.get("function", {}), ensure_ascii=False))
    return int(total)


# ---- classification -------------------------------------------------

def classify_node(state: AgentState, config: RunnableConfig) -> dict:
    """Classify task complexity on the first turn.

    Sets state["classification"] and state["current_model"] based on
    the user's request. Only runs on turn 1.
    """
    classifier_enabled = config.get("configurable", {}).get("classifier_enabled", False)
    available_types = config.get("configurable", {}).get("available_model_types", set())

    if not classifier_enabled:
        current = state.get("current_model", "quick_thinking")
        return {
            "classification": None,
            "current_model": current,
            "node_traces": [{
                "node": "classify",
                "turn": state["turn_count"],
                "status": "skipped",
                "duration_ms": 0,
                "metadata": json.dumps({
                    "skipped": True,
                    "reason": "classifier_disabled",
                }, ensure_ascii=False),
            }],
        }

    classifier_call = config.get("configurable", {}).get("classifier_call")
    if classifier_call is None:
        logger.warning("Classifier enabled but no classifier_call provided, skipping")
        return {"classification": None}

    t0 = time.monotonic()
    classification = classify_request(classifier_call, state.get("messages", []))
    duration_ms = (time.monotonic() - t0) * 1000

    model_type = resolve_model_for_classification(classification, available_types)

    logger.info(
        "Classification: %s -> model=%s (available=%s) in %.0f ms",
        classification, model_type, available_types, duration_ms,
    )

    return {
        "classification": classification,
        "current_model": model_type,
        "node_traces": [{
            "node": "classify",
            "turn": state["turn_count"],
            "status": "ok",
            "duration_ms": round(duration_ms, 1),
            "metadata": json.dumps({
                "classification": classification,
                "resolved_model": model_type,
            }, ensure_ascii=False),
        }],
    }


# ---- compaction ------------------------------------------------------

def compact_node(state: AgentState, config: RunnableConfig) -> dict:
    """Check context usage and compact old messages if over threshold.

    When total estimated tokens exceed the threshold, summarizes older turns,
    keeping the most recent turns that fit within compaction_keep_tokens.
    Supports re-compaction: folds the previous summary into new compaction
    input. Falls back to truncation when the compactor model is unavailable.
    Derives the effective threshold from the model's context_window.
    """
    messages = state.get("messages", [])
    sys_prompt = state.get("system_prompt", "")
    current_tokens = _estimate_tokens(messages) + _text_tokens(sys_prompt)

    compaction_threshold = config.get("configurable", {}).get(
        "compaction_threshold", COMPACTION_THRESHOLD)
    compaction_keep_tokens = config.get("configurable", {}).get(
        "compaction_keep_tokens", COMPACTION_KEEP_TOKENS)

    # Derive threshold from the current model's context_window if available
    model_type = state.get("current_model", "quick_thinking")
    adapter = _resolve_model_adapter(config, model_type)
    if adapter is not None:
        cw = getattr(adapter, 'context_window', 0)
        if cw:
            derived = int(cw * 0.75)
            compaction_threshold = min(compaction_threshold, derived)

    if current_tokens < compaction_threshold:
        return {}

    # Split into turns, then walk backward accumulating token cost
    # until we've kept enough to fill compaction_keep_tokens
    turns = _split_turns(messages)
    keep = []
    kept_tokens = 0
    for turn in reversed(turns):
        turn_tokens = _estimate_tokens(turn)
        if kept_tokens + turn_tokens > compaction_keep_tokens and keep:
            break
        keep.insert(0, turn)
        kept_tokens += turn_tokens

    old = turns[:-len(keep)] if len(keep) < len(turns) else []
    if not old:
        return {}

    compactor_model = config.get("configurable", {}).get("compact_model")
    if compactor_model is not None:
        old_text = _format_turns_for_summary(old)
        # Fold in previous summary on re-compaction
        existing_summary = state.get("context_summary")
        if existing_summary:
            old_text = (
                f"[Previous summary]\n{existing_summary}\n\n"
                f"[New turns to summarize]\n{old_text}"
            )
        cmt = config.get("configurable", {}).get("compactor_max_tokens", 0)
        summary = _call_compactor(compactor_model, old_text, sys_prompt,
                                  max_tokens=cmt if cmt > 0 else None)
    else:
        logger.warning("compact_node: no compact_model, falling back to truncation")
        summary = f"[{len(old)} 轮对话因上下文过长被截断，最早消息已丢弃]"

    result = {
        "messages": list(_flatten_turns(keep)),
        "context_summary": summary,
        "compaction_count": state.get("compaction_count", 0) + 1,
        "node_traces": [{
            "node": "compact",
            "turn": state.get("turn_count", 0),
            "status": "ok",
            "duration_ms": 0,
            "metadata": json.dumps({
                "turns_compacted": len(old),
                "turns_kept": len(keep),
                "tokens_before": current_tokens,
                "tokens_kept": kept_tokens,
                "threshold": compaction_threshold,
            }, ensure_ascii=False),
        }],
    }

    tc = config.get("configurable", {}).get("trace_collector")
    if tc:
        tc.emit({
            "event_type": "lifecycle.compaction",
            "turn": state.get("turn_count", 0),
            "status": "ok",
            "metadata": {
                "turns_compacted": len(old),
                "turns_kept": len(keep),
                "tokens_before": current_tokens,
                "tokens_kept": kept_tokens,
                "threshold": compaction_threshold,
            },
        })

    return result


def _split_turns(messages: list[dict]) -> list[list[dict]]:
    """Split a flat message list into turns (user message + everything after it)."""
    turns: list[list[dict]] = []
    current: list[dict] = []
    for m in messages:
        if m.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(m)
    if current:
        turns.append(current)
    return turns


def _flatten_turns(turns: list[list[dict]]) -> list[dict]:
    """Flatten turns back into a flat message list."""
    result: list[dict] = []
    for t in turns:
        result.extend(t)
    return result


def _format_turns_for_summary(turns: list[list[dict]]) -> str:
    """Format turns into a compact text representation for the compactor."""
    lines = []
    for i, turn in enumerate(turns, 1):
        lines.append(f"\n## Turn {i}")
        for m in turn:
            role = m.get("role", "?")
            content = m.get("content", "") or ""
            # Truncate tool results for summary
            if role in ("tool", "tool_result"):
                content = (content or "")[:300]
                lines.append(f"[{role}]: {content}")
            else:
                lines.append(f"[{role}]: {content[:500]}")
    return "\n".join(lines)


def _call_compactor(compactor_model, old_text: str, sys_prompt: str,
                    max_tokens: int | None = None) -> str:
    """Call the compaction model to summarize old conversation turns."""
    prompt = (
        "你是一个对话摘要助手。请将以下对话历史压缩为一个结构化摘要。\n\n"
        "要求：\n"
        "1. 保留关键决策、重要事实和未完成的任务\n"
        "2. 合并重复信息，删除闲聊和无意义回复\n"
        "3. 保留所有文件路径、代码片段和工具调用结果的关键信息\n"
        "4. 输出为 Markdown 格式，不超过 2000 字\n\n"
        "系统背景：\n"
        f"{sys_prompt[:500]}\n\n"
        "对话历史：\n"
        f"{old_text}\n\n"
        "结构化摘要："
    )
    msgs = [{"role": "user", "content": prompt}]
    try:
        result = compactor_model.chat_complete(msgs, max_tokens=max_tokens)
        content = getattr(result, "content", "") or ""
        return content[:3000]  # cap summary at 3000 chars
    except Exception as e:
        logger.warning("Compaction call failed: %s", e)
        return f"[自动摘要失败: {e}]"


# ---- model calling --------------------------------------------------

def _resolve_model_adapter(config: dict, model_type: str):
    """Resolve the ModelAdapter for a given model type from config."""
    resolvers = config.get("configurable", {}).get("model_resolvers", {})
    resolver = resolvers.get(model_type)
    if resolver:
        return resolver()
    # Fallback: use default resolver
    default = config.get("configurable", {}).get("model_adapter_factory")
    if default:
        return default(model_type)
    return None


def call_model_node(state: AgentState, config: RunnableConfig) -> dict:
    """Call the model with current messages and tools.

    Resolves the model adapter based on state["current_model"],
    builds the full message list (system prompt + conversation),
    and returns the model's response.
    """
    model_type = state.get("current_model", "quick_thinking")
    adapter = _resolve_model_adapter(config, model_type)
    if adapter is None:
        return {
            "last_error": f"No model adapter for type {model_type!r}",
            "final_response": f"模型 {model_type!r} 未配置。请检查模型设置。",
            "truncated": True,
        }

    # Build messages: system prompt first, then conversation
    msgs = [{"role": "system", "content": state["system_prompt"]}]
    context_summary = state.get("context_summary")
    if context_summary:
        msgs.append({"role": "system", "content": f"[Earlier conversation summary]\n{context_summary}"})
    msgs.extend(list(state["messages"]))

    # Emit prompt_snapshot trace
    tc = config.get("configurable", {}).get("trace_collector")
    if tc:
        from arf.server.trace_collector import compute_prompt_hash
        prompt_text = state["system_prompt"]
        prompt_hash = compute_prompt_hash(prompt_text)
        tools = state.get("tools")
        tc.emit({
            "event_type": "lifecycle.prompt_snapshot",
            "turn": state["turn_count"],
            "model": model_type,
            "metadata": {
                "prompt_hash": prompt_hash,
                "prompt_length": len(prompt_text),
                "active_tools_count": len(tools) if tools else 0,
                "tools_list": [t["function"]["name"] for t in tools] if tools else [],
            },
        })
        try:
            from arf.server.database import insert_prompt
            insert_prompt(prompt_hash, prompt_text)
        except Exception:
            pass

    tools = state.get("tools")
    if state.get("_needs_tools_refresh"):
        refresh_tools = config.get("configurable", {}).get("refresh_tools")
        if refresh_tools:
            tools = refresh_tools()

    hook_runner = config.get("configurable", {}).get("hook_runner")
    node_traces: list[dict] = []

    # PreModelCall hook
    pre_model_input = _last_user_message_snippet(state["messages"])
    if hook_runner:
        ht0 = time.monotonic()
        hook_runner("PreModelCall", {
            "model": model_type,
            "turn": state["turn_count"],
            "input_snippet": pre_model_input,
            "message_count": len(state.get("messages", [])),
        })
        hook_pre_dur = (time.monotonic() - ht0) * 1000
        node_traces.append({
            "node": "hook",
            "model": model_type,
            "turn": state["turn_count"],
            "status": "ok",
            "duration_ms": round(hook_pre_dur, 1),
            "metadata": json.dumps({
                "hook_event": "PreModelCall",
                "hook_status": "continue",
            }, ensure_ascii=False),
        })

    # Pre-flight token budget: limit response tokens so prompt+response fit
    # within the model's context_window.  If there's almost no room, the call
    # still proceeds (with a minimal budget) — the API will return an error if
    # it truly cannot fit, and the next compaction cycle will reduce context.
    token_safety_margin = config.get("configurable", {}).get(
        "token_safety_margin", 5000)

    cw = getattr(adapter, 'context_window', 0)
    if cw:
        estimated = _estimate_tokens(msgs)
        safe_max_tokens = max(1, cw - estimated - token_safety_margin)
    else:
        safe_max_tokens = None

    t0 = time.monotonic()
    try:
        response = adapter.chat_complete(msgs, tools=tools, max_tokens=safe_max_tokens)
    except Exception as e:
        error_msg = getattr(e, "message", str(e))
        status = getattr(e, "status_code", 0)
        logger.warning("Model call failed (model=%s): %d %s", model_type, status, error_msg)
        error_dur = round((time.monotonic() - t0) * 1000, 1)
        node_traces.append({
            "node": "call_model",
            "model": model_type,
            "turn": state["turn_count"],
            "status": "error",
            "error_msg": error_msg,
            "duration_ms": error_dur,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "metadata": json.dumps(
                {
                    "status_code": status,
                    "model_input_snippet": pre_model_input,
                },
                ensure_ascii=False,
            ),
        })
        # PostModelCall hook for error
        if hook_runner:
            ht0 = time.monotonic()
            hook_runner("PostModelCall", {
                "model": model_type,
                "turn": state["turn_count"],
                "status": "error",
                "duration_ms": error_dur,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "finish_reason": "",
                "output_snippet": error_msg[:1000],
            })
            hook_post_dur = (time.monotonic() - ht0) * 1000
            node_traces.append({
                "node": "hook",
                "model": model_type,
                "turn": state["turn_count"],
                "status": "error",
                "duration_ms": round(hook_post_dur, 1),
                "metadata": json.dumps({
                    "hook_event": "PostModelCall",
                    "hook_status": "error",
                }, ensure_ascii=False),
            })
        return {
            "last_error": f"API error (HTTP {status}): {error_msg}",
            "turn_count": state["turn_count"] + 1,
            "transition": "api_error_recovery",
            "node_traces": node_traces,
        }

    duration_ms = (time.monotonic() - t0) * 1000

    finish = getattr(response, "finish_reason", None)
    content = str(response.content or "")
    reasoning = getattr(response, "reasoning_content", None)

    # Accumulate usage
    usage = getattr(response, "usage", {}) or {}

    input_snippet = _last_user_message_snippet(state["messages"])
    output_snippet = content[:1000]

    trace = {
        "node": "call_model",
        "model": model_type,
        "turn": state["turn_count"],
        "status": "ok",
        "duration_ms": round(duration_ms, 1),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "metadata": json.dumps(
            {
                "finish_reason": str(finish) if finish else None,
                "has_tool_calls": bool(response.tool_calls),
                "model_input_snippet": input_snippet,
                "model_output_snippet": output_snippet,
            },
            ensure_ascii=False,
        ),
    }

    node_traces.append(trace)

    # PostModelCall hook
    if hook_runner:
        ht0 = time.monotonic()
        hook_runner("PostModelCall", {
            "model": model_type,
            "turn": state["turn_count"],
            "status": "ok",
            "duration_ms": round(duration_ms, 1),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "finish_reason": finish or "",
            "output_snippet": output_snippet,
        })
        hook_post_dur = (time.monotonic() - ht0) * 1000
        node_traces.append({
            "node": "hook",
            "model": model_type,
            "turn": state["turn_count"],
            "status": "ok",
            "duration_ms": round(hook_post_dur, 1),
            "metadata": json.dumps({
                "hook_event": "PostModelCall",
                "hook_status": "continue",
            }, ensure_ascii=False),
        })

    result: dict[str, Any] = {
        "usage": usage,
        "turn_count": state["turn_count"] + 1,
        "node_traces": node_traces,
    }

    # Build assistant message
    if response.tool_calls:
        # Tool call continuation
        assistant_msg: dict = {
            "role": "assistant",
            "content": content if content else None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in response.tool_calls
            ],
        }
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning
        result["messages"] = [assistant_msg]
        result["transition"] = "tool_result_continuation"
    elif finish == "length":
        # Max tokens recovery
        assistant_msg = {"role": "assistant", "content": content}
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning
        result["messages"] = [assistant_msg]
        result["transition"] = "max_tokens_recovery"
        result["continuation_count"] = state.get("continuation_count", 0) + 1
    else:
        # Normal terminal
        assistant_msg = {"role": "assistant", "content": content}
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning
        result["messages"] = [assistant_msg]
        result["transition"] = None
        result["final_response"] = content

    return result


# ---- model calling (streaming) ---------------------------------------

async def call_model_node_stream(state: AgentState, config: RunnableConfig) -> dict:
    """Streaming model call node -- emits token chunks via get_stream_writer().

    Uses the stream_model callable from config to get real-time token
    events and forwards them as custom stream events. Tool calls, usage,
    and the final response are emitted during the stream.
    """
    model_type = state.get("current_model", "quick_thinking")
    adapter = _resolve_model_adapter(config, model_type)
    if adapter is None:
        return {
            "last_error": f"No model adapter for type {model_type!r}",
            "final_response": f"模型 {model_type!r} 未配置。请检查模型设置。",
            "truncated": True,
        }

    # Build messages
    msgs = [{"role": "system", "content": state["system_prompt"]}]
    context_summary = state.get("context_summary")
    if context_summary:
        msgs.append({"role": "system", "content": f"[Earlier conversation summary]\n{context_summary}"})
    msgs.extend(list(state["messages"]))

    # Emit prompt_snapshot trace
    tc = config.get("configurable", {}).get("trace_collector")
    if tc:
        from arf.server.trace_collector import compute_prompt_hash
        prompt_text = state["system_prompt"]
        prompt_hash = compute_prompt_hash(prompt_text)
        tools = state.get("tools")
        tc.emit({
            "event_type": "lifecycle.prompt_snapshot",
            "turn": state["turn_count"],
            "model": model_type,
            "metadata": {
                "prompt_hash": prompt_hash,
                "prompt_length": len(prompt_text),
                "active_tools_count": len(tools) if tools else 0,
                "tools_list": [t["function"]["name"] for t in tools] if tools else [],
            },
        })
        try:
            from arf.server.database import insert_prompt
            insert_prompt(prompt_hash, prompt_text)
        except Exception:
            pass

    tools = state.get("tools")
    if state.get("_needs_tools_refresh"):
        refresh_tools = config.get("configurable", {}).get("refresh_tools")
        if refresh_tools:
            tools = refresh_tools()

    # Pre-flight token budget: if context is nearly full, fail early
    cw = getattr(adapter, 'context_window', 0)
    # Pre-flight token budget (same logic as call_model_node)
    token_safety_margin = config.get("configurable", {}).get(
        "token_safety_margin", 5000)
    cw = getattr(adapter, 'context_window', 0)
    if cw:
        estimated = _estimate_tokens(msgs)
        safe_max_tokens = max(1, cw - estimated - token_safety_margin)
    else:
        safe_max_tokens = None

    stream_call = config.get("configurable", {}).get("stream_model")
    if stream_call is None:
        # Fallback: use non-streaming chat_complete
        logger.warning("No stream_model in config, falling back to non-streaming")
        return call_model_node(state, config)

    hook_runner = config.get("configurable", {}).get("hook_runner")
    node_traces: list[dict] = []
    pre_model_input = _last_user_message_snippet(state["messages"])

    # PreModelCall hook
    if hook_runner:
        ht0 = time.monotonic()
        hook_runner("PreModelCall", {
            "model": model_type,
            "turn": state["turn_count"],
            "input_snippet": pre_model_input,
            "message_count": len(state.get("messages", [])),
        })
        hook_pre_dur = (time.monotonic() - ht0) * 1000
        node_traces.append({
            "node": "hook",
            "model": model_type,
            "turn": state["turn_count"],
            "status": "ok",
            "duration_ms": round(hook_pre_dur, 1),
            "metadata": json.dumps({
                "hook_event": "PreModelCall",
                "hook_status": "continue",
            }, ensure_ascii=False),
        })

    t0 = time.monotonic()
    writer = get_stream_writer()

    text_content = ""
    reasoning_content = ""
    tool_calls_received: list[dict] = []
    usage_acc: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    stream_error = None

    try:
        for event in stream_call(msgs, tools, max_tokens=safe_max_tokens):
            etype = event.get("type", "")
            if etype == "chunk":
                text_content += event.get("content", "")
                reasoning_content += event.get("reasoning", "")
                writer(event)
            elif etype == "tool_call":
                tool_calls_received.append(event)
                writer(event)
            elif etype == "usage":
                for k in usage_acc:
                    usage_acc[k] += event.get(k, 0)
                writer(event)
            elif etype == "error":
                stream_error = event
                writer(event)
                break
    except Exception as e:
        logger.warning("Stream model call failed: %s", e)
        stream_error = {"type": "error", "detail": str(e)}

    duration_ms = (time.monotonic() - t0) * 1000

    output_snippet = text_content[:1000]
    stream_status = "error" if stream_error else "ok"

    node_traces.append({
        "node": "call_model",
        "model": model_type,
        "turn": state["turn_count"],
        "status": stream_status,
        "duration_ms": round(duration_ms, 1),
        "prompt_tokens": usage_acc.get("prompt_tokens", 0),
        "completion_tokens": usage_acc.get("completion_tokens", 0),
        "total_tokens": usage_acc.get("total_tokens", 0),
        "error_msg": stream_error.get("detail", "") if stream_error else None,
        "metadata": json.dumps(
            {
                "has_tool_calls": bool(tool_calls_received),
                "model_input_snippet": pre_model_input,
                "model_output_snippet": output_snippet,
            },
            ensure_ascii=False,
        ),
    })

    # PostModelCall hook
    if hook_runner:
        ht0 = time.monotonic()
        hook_runner("PostModelCall", {
            "model": model_type,
            "turn": state["turn_count"],
            "status": stream_status,
            "duration_ms": round(duration_ms, 1),
            "prompt_tokens": usage_acc.get("prompt_tokens", 0),
            "completion_tokens": usage_acc.get("completion_tokens", 0),
            "total_tokens": usage_acc.get("total_tokens", 0),
            "finish_reason": "",
            "output_snippet": output_snippet,
        })
        hook_post_dur = (time.monotonic() - ht0) * 1000
        node_traces.append({
            "node": "hook",
            "model": model_type,
            "turn": state["turn_count"],
            "status": stream_status,
            "duration_ms": round(hook_post_dur, 1),
            "metadata": json.dumps({
                "hook_event": "PostModelCall",
                "hook_status": stream_status,
            }, ensure_ascii=False),
        })

    result: dict[str, Any] = {
        "usage": usage_acc,
        "turn_count": state["turn_count"] + 1,
        "node_traces": node_traces,
    }

    if stream_error:
        result["last_error"] = stream_error.get("detail", "Stream error")
        result["transition"] = "api_error_recovery"
        return result

    # Build assistant message from streamed content
    if tool_calls_received:
        assistant_msg: dict = {
            "role": "assistant",
            "content": text_content if text_content else None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc.get("name", tc.get("tool", "")),
                        "arguments": tc.get("arguments", "{}"),
                    },
                }
                for tc in tool_calls_received
            ],
        }
        if reasoning_content:
            assistant_msg["reasoning_content"] = reasoning_content
        result["messages"] = [assistant_msg]
        result["transition"] = "tool_result_continuation"
    else:
        assistant_msg = {"role": "assistant", "content": text_content}
        if reasoning_content:
            assistant_msg["reasoning_content"] = reasoning_content
        result["messages"] = [assistant_msg]
        result["transition"] = None
        result["final_response"] = text_content

    return result

def _parse_args(arguments: str) -> dict:
    """Parse tool arguments from JSON string."""
    try:
        return json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
    except (json.JSONDecodeError, TypeError):
        return {}


def _tool_result_has_error(result_str: str) -> bool:
    """Check if a tool result string contains an error."""
    if not result_str:
        return False
    try:
        obj = json.loads(result_str)
        return isinstance(obj, dict) and "error" in obj
    except (json.JSONDecodeError, TypeError):
        return False


def _resolve_model_type_from_name(active_name: str, config: RunnableConfig) -> str | None:
    """Map a model name (e.g. 'deep_thinking') to its model_type by checking configurable."""
    if config is None:
        return None
    available = config.get("configurable", {}).get("available_model_types", set())
    if active_name in available:
        return active_name
    resolvers = config.get("configurable", {}).get("model_resolvers", {})
    if active_name in resolvers:
        return active_name
    return None


def _resolve_model_switch(tool_calls: list[dict], tool_results: list[dict], config: RunnableConfig = None) -> dict:
    """If model_switch was called successfully, update current_model.

    model_switch updates config but doesn't change AgentState.current_model,
    so the next call_model turn would still resolve the old adapter.
    This extracts the switched model_type from the tool result and returns
    a state update so the engine uses the new model immediately.
    """
    for tc in tool_calls:
        tool_name = tc.get("function", {}).get("name", "")
        args_str = tc.get("function", {}).get("arguments", "{}")
        call_id = tc.get("id", "")

        if tool_name == "model_switch":
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
                target = args.get("target", "")
                for tr in tool_results:
                    if tr.get("tool_call_id") == call_id:
                        result_str = tr.get("content", "")
                        result = json.loads(result_str) if isinstance(result_str, str) else result_str
                        if isinstance(result, dict) and result.get("ok"):
                            mt = result.get("model_type", "")
                            if mt:
                                logger.info("model_switch: updating current_model → %s", mt)
                                tc = config.get("configurable", {}).get("trace_collector") if config else None
                                if tc:
                                    tc.emit({
                                        "event_type": "lifecycle.model_switch",
                                        "turn": 0,
                                        "status": "ok",
                                        "metadata": {
                                            "to_model": mt,
                                            "tool": "model_switch",
                                        },
                                    })
                                return {"current_model": mt}
                        break
            except (json.JSONDecodeError, TypeError):
                pass

        elif tool_name == "model_manager":
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
                if args.get("action") == "switch":
                    for tr in tool_results:
                        if tr.get("tool_call_id") == call_id:
                            result_str = tr.get("content", "")
                            result = json.loads(result_str) if isinstance(result_str, str) else result_str
                            if isinstance(result, dict) and result.get("ok"):
                                active_name = result.get("active_model", "")
                                if active_name:
                                    resolved = _resolve_model_type_from_name(active_name, config)
                                    if resolved:
                                        logger.info("model_manager switch: current_model -> %s (%s)",
                                                    active_name, resolved)
                                        tc = config.get("configurable", {}).get("trace_collector") if config else None
                                        if tc:
                                            tc.emit({
                                                "event_type": "lifecycle.model_switch",
                                                "turn": 0,
                                                "status": "ok",
                                                "metadata": {
                                                    "to_model": active_name,
                                                    "tool": "model_manager",
                                                },
                                            })
                                        return {"current_model": resolved}
                            break
            except (json.JSONDecodeError, TypeError):
                pass

    return {}


def _detect_contradictions(
    prior_messages: list[dict],
    new_results: list[dict],
    tool_calls: list[dict],
) -> list[str]:
    """Detect when tool results contradict prior assistant claims."""
    notes = []

    last_assistant = None
    for m in reversed(prior_messages):
        if m.get("role") == "assistant" and m.get("content"):
            last_assistant = m["content"][:1000]
            break

    if not last_assistant:
        return notes

    last_assistant_lower = last_assistant.lower()

    for tc, tr in zip(tool_calls, new_results):
        tool_name = tc.get("function", {}).get("name", "")
        if tool_name in ("model_manager", "model_switch"):
            result_str = tr.get("content", "")
            try:
                result = json.loads(result_str)
            except (json.JSONDecodeError, TypeError):
                continue

            if isinstance(result, dict):
                models = result.get("models", [])
                for model_entry in models:
                    if model_entry.get("active") and model_entry.get("name"):
                        actual_active = model_entry["name"]
                        for claimed_name in ("quick_no_thinking", "quick_thinking", "deep_thinking"):
                            if claimed_name in last_assistant_lower and claimed_name != actual_active:
                                notes.append(
                                    f"Assistant claimed {claimed_name!r} but actual active model "
                                    f"is {actual_active!r}. Correct your response."
                                )

    return notes


def _is_resource_creation(path: str) -> bool:
    """Check if a file_writer path is creating a new tool/skill resource."""
    return (
        ("tools/" in path or "skills/" in path) and
        (path.endswith("tool.yaml") or path.endswith("skill.yaml") or
         path.endswith("function.py"))
    )


def execute_tools_node(state: AgentState, config: RunnableConfig) -> dict:
    """Execute tool calls from the last assistant message.

    Runs PreToolUse/PostToolUse hooks, tracks tool failure counts,
    and appends tool result messages.
    """
    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else {}

    tool_calls = last_msg.get("tool_calls", [])
    if not tool_calls:
        logger.warning("execute_tools_node called with no tool_calls in last message")
        return {"transition": None}

    tool_executor = config.get("configurable", {}).get("tool_executor")
    hook_runner = config.get("configurable", {}).get("hook_runner")

    new_messages: list[dict] = []
    new_events: list[dict] = []
    tool_fails: dict[str, int] = dict(state.get("tool_fail_counts", {}))
    node_traces: list[dict] = []
    seen_calls: set[tuple[str, str]] = set()

    for tc in tool_calls:
        tool_name = tc.get("function", {}).get("name", "unknown")
        arguments = tc.get("function", {}).get("arguments", "{}")
        call_id = tc.get("id", "")
        system_tool_names = config.get("configurable", {}).get("system_tool_names", frozenset())
        tool_category = "sys" if tool_name in system_tool_names else "user"

        t0 = time.monotonic()

        # Deduplicate: same tool + same args in this batch → skip
        call_key = (tool_name, arguments)
        if call_key in seen_calls:
            logger.info("Skipping duplicate call: %s(%s)", tool_name, arguments[:100])
            new_messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps({
                    "ok": True,
                    "deduplicated": True,
                    "note": "Duplicate call skipped — same tool+args already executed this turn.",
                }),
            })
            node_traces.append({
                "node": "execute_tools",
                "tool_name": tool_name,
                "turn": state["turn_count"],
                "status": "skipped",
                "metadata": json.dumps({"deduplicated": True}, ensure_ascii=False),
            })
            continue
        seen_calls.add(call_key)

        # Parse arguments for hook payload
        tool_input = _parse_args(arguments)

        # PreToolUse hook
        hook_pre_status = None
        if hook_runner:
            ht0 = time.monotonic()
            hook_result = hook_runner("PreToolUse", {
                "tool_name": tool_name,
                "tool_category": tool_category,
                "tool_input": tool_input,
            })
            hook_pre_dur = (time.monotonic() - ht0) * 1000
            if hook_result and hook_result.get("blocked"):
                hook_pre_status = "blocked"
                blocked_msg = f"[Hook blocked]: {hook_result.get('reason', 'Blocked')}"
                new_messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": blocked_msg,
                })
                new_events.append({
                    "type": "tool_result",
                    "tool": tool_name,
                    "id": call_id,
                    "result": blocked_msg,
                })
                node_traces.append({
                    "node": "hook",
                    "tool_name": tool_name,
                    "turn": state["turn_count"],
                    "status": "blocked_by_hook",
                    "duration_ms": round(hook_pre_dur, 1),
                    "metadata": json.dumps({
                        "hook_event": "PreToolUse",
                        "hook_status": "blocked",
                        "hook_message": hook_result.get("reason", ""),
                        "tool_category": tool_category,
                    }, ensure_ascii=False),
                })
                node_traces.append({
                    "node": "execute_tools",
                    "tool_name": tool_name,
                    "turn": state["turn_count"],
                    "status": "blocked_by_hook",
                })
                continue
            if hook_result and hook_result.get("inject"):
                hook_pre_status = "inject"
                new_messages.append({
                    "role": "user",
                    "content": f"[Hook message]: {hook_result['inject']}",
                })
            else:
                hook_pre_status = "continue"
            node_traces.append({
                "node": "hook",
                "tool_name": tool_name,
                "turn": state["turn_count"],
                "status": "ok",
                "duration_ms": round(hook_pre_dur, 1),
                "metadata": json.dumps({
                    "hook_event": "PreToolUse",
                    "hook_status": hook_pre_status,
                    "hook_message": hook_result.get("reason", "") or hook_result.get("inject", "") if hook_result else "",
                    "tool_category": tool_category,
                }, ensure_ascii=False),
            })

        # Execute tool
        if tool_executor is None:
            result = json.dumps({"error": "No tool executor configured"})
        else:
            result = tool_executor(tool_name, arguments)

        # Progressive disclosure: if result is too long, save full to disk
        # and put only a summary + pointer in the context
        stored_path = ""
        if len(result) > _TOOL_OUTPUT_THRESHOLD:
            workspace_dir = config.get("configurable", {}).get("workspace_dir", "")
            if workspace_dir:
                import time as _time
                from pathlib import Path as _Path
                results_dir = _Path(workspace_dir) / "tool_results"
                results_dir.mkdir(parents=True, exist_ok=True)
                ts = _time.strftime("%Y%m%d_%H%M%S")
                safe_name = tool_name.replace("/", "_").replace("\\", "_")
                fname = f"{ts}_{safe_name}_{call_id}.txt"
                fpath = results_dir / fname
                fpath.write_text(result, encoding="utf-8")
                stored_path = str(fpath)
                result = (
                    result[:500] + "\n\n"
                    f"... [输出截断: 完整结果 {len(result)} 字符, "
                    f"已存储: {fpath.name}]\n"
                    f"请用 file_reader 工具读取该文件以获取完整内容: {stored_path}"
                )

        new_messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": result,
        })

        # Record events
        new_events.append({
            "type": "tool_call",
            "tool": tool_name,
            "arguments": arguments,
            "id": call_id,
        })
        new_events.append({
            "type": "tool_result",
            "tool": tool_name,
            "id": call_id,
            "result": result,
        })

        duration_ms = (time.monotonic() - t0) * 1000

        # Track failures
        if _tool_result_has_error(result):
            tool_fails[tool_name] = tool_fails.get(tool_name, 0) + 1
            node_traces.append({
                "node": "execute_tools",
                "tool_name": tool_name,
                "turn": state["turn_count"],
                "status": "error",
                "duration_ms": round(duration_ms, 1),
                "metadata": json.dumps({
                    "tool_category": tool_category,
                    "tool_input_snippet": json.dumps(tool_input, ensure_ascii=False)[:500],
                    "tool_output_snippet": _tool_result_snippet(result),
                    "consecutive_failures": tool_fails[tool_name],
                }, ensure_ascii=False),
            })
        else:
            tool_fails.pop(tool_name, None)
            node_traces.append({
                "node": "execute_tools",
                "tool_name": tool_name,
                "turn": state["turn_count"],
                "status": "ok",
                "duration_ms": round(duration_ms, 1),
                "metadata": json.dumps({
                    "tool_category": tool_category,
                    "tool_input_snippet": json.dumps(tool_input, ensure_ascii=False)[:500],
                    "tool_output_snippet": _tool_result_snippet(result),
                }, ensure_ascii=False),
            })

        # PostToolUse hook
        if hook_runner:
            ht0 = time.monotonic()
            hook_result = hook_runner("PostToolUse", {
                "tool_name": tool_name,
                "tool_category": tool_category,
                "tool_input": tool_input,
                "tool_output": result,
            })
            hook_post_dur = (time.monotonic() - ht0) * 1000
            hook_post_status = "inject" if (hook_result and hook_result.get("inject")) else "continue"
            if hook_result and hook_result.get("inject"):
                new_messages.append({
                    "role": "user",
                    "content": f"[Hook note]: {hook_result['inject']}",
                })
            node_traces.append({
                "node": "hook",
                "tool_name": tool_name,
                "turn": state["turn_count"],
                "status": "ok",
                "duration_ms": round(hook_post_dur, 1),
                "metadata": json.dumps({
                    "hook_event": "PostToolUse",
                    "hook_status": hook_post_status,
                    "hook_message": hook_result.get("reason", "") or hook_result.get("inject", "") if hook_result else "",
                    "tool_category": tool_category,
                }, ensure_ascii=False),
            })

        # 3-consecutive-failure hint
        if tool_fails.get(tool_name, 0) >= 3:
            new_messages.append({
                "role": "user",
                "content": (
                    f"Tool '{tool_name}' has failed {tool_fails[tool_name]} times. "
                    "Consider switching to a more powerful model via `model_switch` "
                    "or reading `@sys/skills/model_switch/skill.yaml` for guidance."
                ),
            })
            tool_fails.pop(tool_name, None)

    # After executing all tools, check if model_switch changed the session model.
    model_update = _resolve_model_switch(tool_calls, new_messages, config)

    # Contradiction detection: if the last assistant message made a claim
    # that a subsequent tool result contradicts, inject a flag.
    if not model_update:
        contradiction_notes = _detect_contradictions(messages, new_messages, tool_calls)
        if contradiction_notes:
            new_messages.append({
                "role": "user",
                "content": "[CONTRADICTION] " + " ".join(contradiction_notes),
            })

    # Post-tool state refresh: if any tool mutated the registry, signal
    # that tools should be rebuilt before the next call_model turn.
    state_update = model_update.copy() if model_update else {}
    registry_mutators = {"resource_loader", "model_switch", "model_manager"}
    if any(
        tc.get("function", {}).get("name", "") in registry_mutators
        for tc in tool_calls
    ):
        state_update["_needs_tools_refresh"] = True

    return {
        "messages": new_messages,
        "tool_events": new_events,
        "tool_fail_counts": tool_fails,
        "transition": "tool_result_continuation",
        "node_traces": node_traces,
        **state_update,
    }


# ---- respond --------------------------------------------------------

def respond_node(state: AgentState, config: RunnableConfig = None) -> dict:
    """Terminal node: ensures final_response is set and marks completion."""
    response = state.get("final_response")
    if response is None:
        # Extract from last assistant message
        messages = state.get("messages", [])
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls"):
                response = m["content"]
                break
        if response is None:
            response = ""

    truncated = state.get("truncated", False) or state.get("turn_count", 0) > state.get("max_turns", 10)

    return {
        "final_response": response,
        "truncated": truncated,
        "node_traces": [{
            "node": "respond",
            "turn": state["turn_count"],
            "status": "ok",
            "metadata": json.dumps({
                "response_snippet": (response or "")[:500],
                "truncated": truncated,
            }, ensure_ascii=False),
        }],
    }


# ---- recovery --------------------------------------------------------

def recovery_node(state: AgentState, config: RunnableConfig = None) -> dict:
    """Handle recovery scenarios: max_tokens continuation and API errors.

    Max tokens: injects a "Continue" message, up to 3 times.
    API error: returns a user-facing error message and truncates.
    """
    last_error = state.get("last_error")
    transition = state.get("transition", "")
    continuation_count = state.get("continuation_count", 0)

    if last_error:
        # API error -- inject error as synthetic response
        error_msg = (
            f"API 调用失败: {last_error}\n\n"
            "请检查工具参数格式是否正确，或检查模型配置。"
        )
        return {
            "final_response": error_msg,
            "truncated": True,
            "node_traces": [{
                "node": "recovery",
                "turn": state["turn_count"],
                "status": "error",
                "error_msg": last_error,
                "metadata": json.dumps({
                    "recovery_type": "api_error",
                    "error_snippet": last_error[:500] if last_error else "",
                }, ensure_ascii=False),
            }],
        }

    if transition == "max_tokens_recovery" and continuation_count < 3:
        return {
            "messages": [{
                "role": "user",
                "content": (
                    "Continue from where you were cut off. "
                    "Do not repeat what you already said."
                ),
            }],
            "node_traces": [{
                "node": "recovery",
                "turn": state["turn_count"],
                "status": "ok",
                "metadata": json.dumps({
                    "recovery_type": "max_tokens",
                    "continuation_count": continuation_count + 1,
                }, ensure_ascii=False),
            }],
        }

    # Recovery exhausted -- end with whatever content we have
    return {
        "truncated": True,
        "node_traces": [{
            "node": "recovery",
            "turn": state["turn_count"],
            "status": "skipped",
            "metadata": json.dumps({
                "recovery_type": "exhausted",
            }, ensure_ascii=False),
        }],
    }
