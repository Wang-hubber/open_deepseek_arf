"""Compaction summarizer — structured summary generation.

Uses a configurable LLM call_model to produce a structured summary from old
messages. Follows Claude Code compaction protocol: summary is injected as a
user message with isCompactSummary=true, after a compact_boundary system marker.
"""

COMPACTION_SYSTEM_PROMPT = """You are a context compaction summarizer. Your task is to compress a conversation history into a structured summary that preserves all critical information.

## Output Format

Generate a summary with these sections. Skip any section that has no relevant content:

### Decisions Made
- Key decisions, agreements, and architectural choices. Include WHY.

### Current Task & Progress
- What task is being worked on right now
- What has been completed so far in this task
- What remains to be done

### Key Context
- Important facts, constraints, and discoveries from the conversation
- User preferences, requirements, and feedback
- Technical details that future turns will need

### Files Modified
- Files changed and what was done to each
- New files created and their purpose

### Open Questions
- Unresolved questions or decisions that need attention

## Rules
- Be concise but complete. Prefer bullet points.
- Preserve code snippets, file paths, and technical identifiers exactly.
- Do NOT summarize tool outputs verbatim — capture only the key findings.
- If the user explicitly asked to remember something, include it.
- Output ONLY the structured summary — no preamble, no "Here is the summary".
"""


async def summarize(
    call_model,
    old_messages: list[dict],
    existing_summary: str = "",
) -> str:
    """Generate a structured summary of old messages.

    Args:
        call_model: async callable(msgs, model, tools=None) → dict with "content" key.
        old_messages: list of message dicts to summarize.
        existing_summary: previous summary text to merge with.

    Returns:
        Structured summary text following the COMPACTION_SYSTEM_PROMPT format.
    """
    prompt = f"""Summarize the following conversation history.

<existing_summary>
{existing_summary or "(none — this is the first compaction)"}
</existing_summary>

<conversation_to_summarize>
{_messages_to_text(old_messages)}
</conversation_to_summarize>

Generate an updated structured summary following the format specified in your system prompt."""

    messages = [
        {"role": "system", "content": COMPACTION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        response = await call_model(messages, model=None, tools=None)
        return response.get("content", "") if isinstance(response, dict) else str(response)
    except Exception:
        return _fallback_summary(old_messages)


def _messages_to_text(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "tool_use":
                        parts.append(f"[tool_call: {c.get('name', '')}]")
                    elif c.get("type") == "tool_result":
                        tc = str(c.get("content", ""))[:300]
                        parts.append(f"[tool_result: {tc}]")
            content = "\n".join(parts)
        if isinstance(content, str) and len(content) > 800:
            content = content[:800] + "..."
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


def _fallback_summary(messages: list[dict]) -> str:
    """Minimal fallback when LLM summarizer is unavailable."""
    user_msgs = [m for m in messages if m.get("role") == "user"]
    topics = []
    for m in user_msgs[-5:]:
        c = m.get("content", "")
        if isinstance(c, str) and len(c) > 10:
            topics.append(c[:120])
    return "### Current Task & Progress\n- " + "\n- ".join(topics) if topics else "(continuation)"
