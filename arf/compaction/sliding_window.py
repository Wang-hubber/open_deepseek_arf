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

        window_size overrides the instance default — used when the engine
        passes the currently-routed model's context window.
        """
        t = threshold or self._threshold
        w = window_size or self._window_size
        last_usage = state.get("last_token_usage", 0)
        limit = int(t * w)
        return last_usage > limit

    async def compact(self, state: AgentState) -> AgentState:
        """Keep last 4 messages, summarize older ones into context_summary."""
        msgs = state.get("messages", [])
        if len(msgs) <= 4:
            return state
        old_msgs = msgs[:-4]
        recent = msgs[-4:]
        summary = state.get("context_summary", "")
        if self._summarize and old_msgs:
            try:
                new_summary = await self._summarize(old_msgs)
                prefix = "[Earlier]" if not summary else ""
                summary = f"{summary}\n{prefix}: {new_summary}" if summary else f"[Earlier]: {new_summary}"
                logger.info("Compaction: %d messages summarized, context_summary now %d chars",
                            len(old_msgs), len(summary))
            except Exception:
                logger.exception("Compaction summarizer failed, discarding old messages")
        else:
            logger.info("Compaction: %d old messages discarded (no summarizer)", len(old_msgs))
        return {**state, "messages": recent, "context_summary": summary}

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
