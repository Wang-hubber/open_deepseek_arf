"""CompactionPlugin — context compaction as a hook-mounted plugin.

Replaces inline compaction logic in GraphEngine.
Mounted on: round_end hook.
"""
import logging
from pathlib import Path

from arf.core.plugin_context import PluginContext
from arf.core.state import AgentState

logger = logging.getLogger("arf.plugins.compaction")

DEFAULT_WINDOW_SIZE = 131_072


class CompactionPlugin:
    """Summarizes old turns when context nears the token limit.

    Fires on round_end. Checks last_token_usage against threshold * window_size.
    When triggered, keeps the last keep_count non-tool messages and summarizes
    older messages via a configurable LLM summarizer.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.threshold: float = cfg.get("threshold", 0.75)
        self.window_size: int = cfg.get("window_size", DEFAULT_WINDOW_SIZE)
        self.keep_count: int = cfg.get("keep_count", 8)
        self._summarizer = None
        self._state_store = None
        self._workspace = Path(cfg.get("workspace", "./data/state"))
        self._cooldown: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "compaction"

    @property
    def hooks(self) -> dict[str, str]:
        return {"round_end": "blocking"}

    def set_summarizer(self, summarizer) -> None:
        self._summarizer = summarizer

    def set_state_store(self, state_store) -> None:
        self._state_store = state_store

    async def on_hook(self, hook_name: str, context: PluginContext) -> None:
        if hook_name == "round_end":
            await self._maybe_compact(context)

    async def _maybe_compact(self, context: PluginContext) -> None:
        sid = context.session_id
        cooldown = self._cooldown.get(sid, 0)
        if cooldown > 0:
            self._cooldown[sid] = cooldown - 1
            return

        if not self._state_store:
            return

        state: AgentState = await self._state_store.get(sid)
        if not state:
            return

        last_usage = state.get("last_token_usage", 0)
        limit = int(self.threshold * self.window_size)
        if last_usage <= limit:
            return

        msgs = state.get("messages", [])
        ua_indices = [i for i, m in enumerate(msgs)
                      if isinstance(m, dict) and m.get("role") != "tool"]
        if len(ua_indices) <= self.keep_count:
            return

        split = ua_indices[-self.keep_count]
        old_msgs = msgs[:split]
        recent = msgs[split:]

        summary = state.get("context_summary", "")
        if self._summarizer and old_msgs:
            try:
                new_summary = await self._summarizer(old_msgs)
                prefix = "\n[Earlier]" if not summary else ""
                summary = f"{summary}{prefix}: {new_summary}" if summary else f"[Earlier]: {new_summary}"
                logger.info("Compaction: %d msgs summarized, summary now %d chars",
                            len(old_msgs), len(summary))
            except Exception:
                logger.exception("Compaction summarizer failed")

        new_state = {**state, "messages": recent, "context_summary": summary}
        await self._state_store.put(sid, new_state)
        self._cooldown[sid] = 2
        logger.info("Compaction: %d msgs compacted for session %s", len(old_msgs), sid)

    async def summarize_tool_output(self, tool_name: str, output: str,
                                     turn: int) -> str:
        """Summarize long tool output. Kept as public method for engine to call."""
        MAX_CHARS = 2000
        if len(output) <= MAX_CHARS:
            return output

        out_dir = self._workspace / "tool_outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"turn_{turn}_{tool_name}.txt"
        out_path.write_text(output, encoding="utf-8")

        truncated = output[:MAX_CHARS]
        if self._summarizer:
            try:
                summary = await self._summarizer([
                    {"role": "tool",
                     "content": f"Tool {tool_name} output (full at {out_path}):\n{truncated}"}
                ])
                return f"[Tool output summarized — full at {out_path}]\n{summary}"
            except Exception:
                logger.exception("Tool output summarization failed")
        return f"[Tool output truncated — full at {out_path}]\n{truncated}..."
