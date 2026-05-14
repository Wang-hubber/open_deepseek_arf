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
    model_type = state.get("current_model", "quick_no_thinking")
    adapter = _resolve_model_adapter(config, model_type)
    if adapter is None:
        return {
            "last_error": f"No model adapter for type {model_type!r}",
            "final_response": f"模型 {model_type!r} 未配置。请检查模型设置。",
            "truncated": True,
        }

    # Build messages: system prompt first, then conversation
    msgs = [{"role": "system", "content": state["system_prompt"]}]
    msgs.extend(list(state["messages"]))

    tools = state.get("tools")

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

    t0 = time.monotonic()
    try:
        response = adapter.chat_complete(msgs, tools=tools)
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
    content = response.content or ""
    reasoning = getattr(response, "reasoning_content", None)

    # Accumulate usage
    usage = getattr(response, "usage", {}) or {}

    input_snippet = _last_user_message_snippet(state["messages"])
    output_snippet = (content or "")[:1000]

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
                "finish_reason": finish,
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
    model_type = state.get("current_model", "quick_no_thinking")
    adapter = _resolve_model_adapter(config, model_type)
    if adapter is None:
        return {
            "last_error": f"No model adapter for type {model_type!r}",
            "final_response": f"模型 {model_type!r} 未配置。请检查模型设置。",
            "truncated": True,
        }

    # Build messages
    msgs = [{"role": "system", "content": state["system_prompt"]}]
    msgs.extend(list(state["messages"]))
    tools = state.get("tools")

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
        for event in stream_call(msgs, tools):
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

    for tc in tool_calls:
        tool_name = tc.get("function", {}).get("name", "unknown")
        arguments = tc.get("function", {}).get("arguments", "{}")
        call_id = tc.get("id", "")
        tool_category = "sys" if tool_name.startswith("@sys/") else "user"

        t0 = time.monotonic()

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

    return {
        "messages": new_messages,
        "tool_events": new_events,
        "tool_fail_counts": tool_fails,
        "transition": "tool_result_continuation",
        "node_traces": node_traces,
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
