"""SlidingWindowCompactor — summarise old turns when context nears limit.

Uses the previous turn's model call token usage to decide when to compact.
When triggered, keeps the last 4 messages and summarizes older turns via LLM.
Also provides tool output summarization: long results → summary in context, raw on disk.
"""
import logging
from pathlib import Path

from arf.core.state import AgentState

logger = logging.getLogger("arf.compaction")

# DeepSeek V4 context window (tokens)
DEFAULT_WINDOW_SIZE = 131_072


class SlidingWindowCompactor:
    """Context compactor triggered by token usage from the previous model call.

    After each model call, the engine stores usage.total_tokens in state.
    On the next turn, if usage exceeds threshold * window_size, compaction
    fires before the model call — keeping the last 4 messages and
    summarizing older ones.

    Also handles tool output summarization: long tool results are written
    to disk, with only a summary kept in the conversation context.
    """

    def __init__(self, threshold: float = 0.75, summarizer: callable | None = None,
                 window_size: int = DEFAULT_WINDOW_SIZE, workspace: str | Path = "./memory") -> None:
        self._threshold = threshold
        self._summarize = summarizer
        self._window_size = window_size
        self._workspace = Path(workspace)

    def should_compact(self, state: AgentState, threshold: float | None = None,
                       window_size: int | None = None) -> bool:
        """Trigger when last model call used > threshold * window tokens.

        Cooldown: after compaction, skip 2 rounds to avoid false re-triggers
        (token usage from the large pre-compaction round persists until the
        next model call completes).
        """
        cooldown = state.get("_compaction_cooldown", 0)
        if cooldown > 0:
            return False
        t = threshold or self._threshold
        w = window_size or self._window_size
        last_usage = state.get("last_token_usage", 0)
        limit = int(t * w)
        return last_usage > limit

    async def compact(self, state: AgentState) -> AgentState:
        """Keep last 4 user/assistant messages, discard tool messages,
        summarize older messages into context_summary.
        """
        msgs = state.get("messages", [])
        # Only keep user + assistant messages; tool results are transient
        # Separate dict messages (user/assistant) from tool messages and raw values
        ua_msgs: list[dict] = []
        non_tool: list = []
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "tool":
                continue  # discard tool messages
            non_tool.append(m)
            if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
                ua_msgs.append(m)
        if len(non_tool) <= 4:
            return state
        old_msgs = non_tool[:-4]
        recent = non_tool[-4:]
        summary = state.get("context_summary", "")
        if self._summarize and old_msgs:
            try:
                new_summary = await self._summarize(old_msgs)
                prefix = "[Earlier]" if not summary else ""
                summary = f"{summary}\n{prefix}: {new_summary}" if summary else f"[Earlier]: {new_summary}"
                discarded = len(msgs) - len(non_tool)
                logger.info("Compaction: %d u/a messages summarized (%d tool discarded), "
                            "context_summary now %d chars",
                            len(old_msgs), discarded, len(summary))
            except Exception:
                logger.exception("Compaction summarizer failed, discarding old messages")
        else:
            discarded = len(msgs) - len(non_tool)
            logger.info("Compaction: %d u/a messages discarded (%d tool, no summarizer)",
                        len(old_msgs), discarded)
        return {**state, "messages": recent, "context_summary": summary,
                "_compaction_cooldown": 2}

    async def summarize_tool_output(self, tool_name: str, output: str, turn: int) -> str:
        """Summarize a long tool output. Saves raw to disk, returns summary for context.

        Returns the original string if it's short enough, otherwise a summary
        with a reference to the on-disk file.
        """
        MAX_CHARS = 2000
        if len(output) <= MAX_CHARS:
            return output

        # Save raw output to disk
        out_dir = self._workspace / "tool_outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"turn_{turn}_{tool_name}.txt"
        out_path.write_text(output, encoding="utf-8")

        truncated = output[:MAX_CHARS]
        if self._summarize:
            try:
                summary = await self._summarize(
                    [{"role": "tool", "content": f"Tool {tool_name} output (full at {out_path}):\n{truncated}"}]
                )
                return f"[Tool output summarized — full at {out_path}]\n{summary}"
            except Exception:
                logger.exception("Tool output summarization failed, falling back to truncation")

        return f"[Tool output truncated — full at {out_path}]\n{truncated}..."
