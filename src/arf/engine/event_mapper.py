"""Streaming event mapper -- LangGraph astream_events -> ARF event protocol.

Maps LangGraph's v2 streaming events to ARF's established event types:
  {"type": "chunk", "content": "...", "reasoning": "..."}
  {"type": "tool_call", "name": "...", "arguments": "...", "id": "..."}
  {"type": "tool_result", "tool": "...", "id": "...", "result": "..."}
  {"type": "usage", "prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}
  {"type": "done", "response": "...", "history": [...], "truncated": false}
  {"type": "error", "detail": "..."}
"""

import logging
from typing import AsyncGenerator, Any, Optional

logger = logging.getLogger("arf.graph.stream")


async def map_stream_events(
    graph,
    initial_state: dict,
    config: dict,
) -> AsyncGenerator[dict, None]:
    """Stream events from a LangGraph compiled graph to ARF event dicts.

    Wraps graph.astream_events() and maps events to ARF's protocol.

    Yields:
        dict: ARF event types (chunk, tool_call, tool_result, usage, done, error)
    """
    done_yielded = False
    final_state = None

    try:
        async for event in graph.astream_events(
            initial_state, config, version="v2"
        ):
            kind = event.get("event", "")
            name = event.get("name", "")
            data = event.get("data", {})

            # ---- chat model stream (token-level) ----
            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                # Extract text delta
                content = getattr(chunk, "content", None)
                if content == "":
                    content = None
                # DeepSeek reasoning_content
                reasoning = None
                if hasattr(chunk, "reasoning_content"):
                    reasoning = chunk.reasoning_content
                elif hasattr(chunk, "model_extra"):
                    extra = chunk.model_extra or {}
                    reasoning = extra.get("reasoning_content")

                if reasoning and not content:
                    yield {"type": "chunk", "content": "", "reasoning": reasoning}
                elif content:
                    yield {"type": "chunk", "content": content, "reasoning": reasoning or ""}

            # ---- chat model end (tool calls + usage) ----
            elif kind == "on_chat_model_end":
                output = data.get("output")
                if output is None:
                    continue

                # Tool calls
                tool_calls = getattr(output, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        yield {
                            "type": "tool_call",
                            "name": tc.function.name if hasattr(tc, "function") else tc.get("name", ""),
                            "arguments": tc.function.arguments if hasattr(tc, "function") else tc.get("arguments", ""),
                            "id": tc.id if hasattr(tc, "id") else tc.get("id", ""),
                        }

                # Usage
                usage = getattr(output, "usage", None)
                if usage:
                    yield {
                        "type": "usage",
                        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                    }

            # ---- tool end (tool results) ----
            elif kind == "on_tool_end":
                output = data.get("output")
                if output:
                    # ToolEnd output is the tool's return value string
                    # We need to find which tool this was -- metadata from the run
                    yield {
                        "type": "tool_result",
                        "tool": name,
                        "id": event.get("run_id", ""),
                        "result": str(output) if not isinstance(output, str) else output,
                    }

            # ---- chain end (graph node completion) ----
            elif kind == "on_chain_end" and name != "LangGraph":
                # Record final state from the last chain end
                chain_output = data.get("output")
                if isinstance(chain_output, dict) and "final_response" in chain_output:
                    final_state = chain_output

        # ---- done event ----
        if not done_yielded:
            response = ""
            history = []
            truncated = False

            if final_state:
                response = final_state.get("final_response", "") or ""
                history = _display_history(final_state.get("messages", []))
                truncated = final_state.get("truncated", False)
            else:
                # Try to get final state from graph.get_state if available
                try:
                    snapshot = graph.get_state(config)
                    if snapshot and snapshot.values:
                        sv = snapshot.values
                        response = sv.get("final_response", "") or ""
                        history = _display_history(sv.get("messages", []))
                        truncated = sv.get("truncated", False)
                except Exception:
                    pass

            yield {
                "type": "done",
                "response": response,
                "history": history,
                "truncated": truncated,
            }
            done_yielded = True

    except Exception as e:
        logger.error("Streaming error: %s", e, exc_info=True)
        if not done_yielded:
            yield {
                "type": "error",
                "detail": str(e),
            }
            yield {
                "type": "done",
                "response": f"Streaming error: {e}",
                "history": [],
                "error": True,
            }


def _display_history(messages: list[dict]) -> list[dict]:
    """Filter messages to only user/assistant content (no tool_calls)."""
    return [
        m for m in messages
        if m.get("role") in ("user", "assistant")
        and "tool_calls" not in m
    ]
