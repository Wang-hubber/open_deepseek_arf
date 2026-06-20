"""CompactionPlugin — compact messages before model call.

Deep port: directly extends Plugin base class, operates on AgentState
with Message objects, no PluginAdapter indirection.
"""
from __future__ import annotations
import hashlib
import logging
from pathlib import Path

from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.agent.state import Message

logger = logging.getLogger("arf.plugins.compaction")


def _count_user_messages(messages: list[Message]) -> list[int]:
    """Return indices of user messages (not compact summaries)."""
    indices = []
    for i, m in enumerate(messages):
        if m.role == "user" and not (
            isinstance(m.content, dict) and m.content.get("isCompactSummary")
        ):
            indices.append(i)
    return indices


class CompactionPlugin(Plugin):
    """Progressive context compaction — truncation of tool outputs + LLM summarization.

    Mounted on before_model (blocking) and after_tools (blocking).
    """

    def __init__(self, name="compaction", events=None, config=None):
        events = events or [
            {"hook_name": "before_model", "event_name": "compact", "mode": "blocking"},
            {"hook_name": "after_tools", "event_name": "safeguard", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})

        self.threshold = self.config.get("threshold", 500)
        self.keep_recent = self.config.get("keep_recent", 10)
        self.preview_chars = self.config.get("preview_chars", 100)
        self._call_model = None  # injected for LLM summarization (optional)
        self._cooldown: dict[str, int] = {}
        self._compaction_count: dict[str, int] = {}

    def set_call_model(self, call_model):
        self._call_model = call_model

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "compact":
            await self._compact(ctx)
        elif event_name == "safeguard":
            await self._safeguard(ctx)

    async def _convert_to_dicts(self, messages: list[Message]) -> list[dict]:
        """Convert Message objects to dicts for compatibility."""
        result = []
        for m in messages:
            d = {"role": m.role, "content": m.content}
            # Preserve compact metadata if present
            if isinstance(m.content, dict):
                for extra in ("isCompactSummary", "subtype", "compactMetadata", "name", "tool_call_id"):
                    if extra in m.content:
                        d[extra] = m.content[extra]
                # Flatten content for processing
                d["content"] = m.content.get("content", "") if "content" in m.content else str(m.content)
            result.append(d)
        return result

    async def _compact(self, ctx: PluginContext) -> None:
        """Perform token-aware window compaction on agent messages."""
        messages = ctx.agent.state.messages
        sid = ctx.session_id

        # Cooldown check
        cooldown = self._cooldown.get(sid, 0)
        if cooldown > 0:
            self._cooldown[sid] = cooldown - 1
            return

        if len(messages) <= self.keep_recent:
            return

        # Find split point: keep recent user/assistant messages
        ua_indices = [i for i, m in enumerate(messages) if m.role != "tool"]
        if len(ua_indices) <= self.keep_recent:
            return

        split = ua_indices[-self.keep_recent]
        kept = messages[split:]

        # Truncate tool messages before the split
        old_count = len(messages)
        compacted_count = old_count - len(kept)

        # Externalize large tool outputs before the split point
        for i in range(split):
            m = messages[i]
            if m.role != "tool":
                continue
            content = m.content
            if isinstance(content, dict):
                result_str = str(content.get("result", content.get("content", "")))
            else:
                result_str = str(content) if content else ""
            if len(result_str) > self.threshold:
                await self._externalize_tool(ctx, messages, i, result_str)

        # Replace messages with compacted version
        # (keep boundary markers for traceability)
        ctx.agent.state.messages = kept

        count = self._compaction_count.get(sid, 0) + 1
        self._compaction_count[sid] = count
        self._cooldown[sid] = 2

        ctx.emit("compaction_end", {
            "compacted_count": compacted_count,
            "kept_count": len(kept),
            "total_compactions": count,
            "round": ctx.interaction_round,
            "turn": ctx.turn,
        })

        logger.info(
            "Compaction #%d: %d msgs compacted, %d msgs kept (session=%s)",
            count, compacted_count, len(kept), sid,
        )

    async def _externalize_tool(
        self, ctx: PluginContext, messages: list[Message], index: int, content: str
    ) -> None:
        """Write oversized tool output to disk, replace with preview."""
        m = messages[index]
        tool_name = "unknown"
        if isinstance(m.content, dict):
            tool_name = m.content.get("name", "unknown")

        content_hash = hashlib.sha1(content.encode()).hexdigest()[:8]
        output_dir = Path(ctx.data_dir) / ctx.session_id / "tool_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"turn_{ctx.turn}_{tool_name}_{content_hash}.txt"

        try:
            output_path.write_text(content, encoding="utf-8")
        except OSError:
            logger.warning("Failed to write externalized tool output to %s", output_path)
            return

        preview = content[:self.preview_chars]
        new_content = (
            f"[Tool output externalized — {len(content)} chars, full at {output_path}]\n"
            f"{preview}..."
        )

        if isinstance(m.content, dict):
            new_content_dict = {**m.content, "result": new_content, "content": new_content}
            messages[index] = Message(message_id=m.message_id, role=m.role, content=new_content_dict)
        else:
            messages[index] = Message(message_id=m.message_id, role=m.role, content=new_content)

        ctx.emit("safeguard_triggered", {
            "tool_name": tool_name,
            "original_chars": len(content),
            "round": ctx.interaction_round,
            "turn": ctx.turn,
        })

    async def _safeguard(self, ctx: PluginContext) -> None:
        """Check raw tool results for oversized outputs after tool execution."""
        raw_results = ctx.hook_data.get("_raw_tool_results", {})
        if not raw_results:
            return

        for tc_id, r in raw_results.items():
            result_data = r.get("data", "") if isinstance(r, dict) else str(r)
            if not result_data or len(str(result_data)) <= self.threshold:
                continue
            # Mark that externalization is needed
            # (the actual truncation will happen at the next before_model checkpoint)
            tool_name = r.get("tool_name", "") if isinstance(r, dict) else ""
            logger.debug(
                "Safeguard: large tool output detected for %s (%d chars) session=%s turn=%d",
                tool_name, len(str(result_data)), ctx.session_id, ctx.turn,
            )


Plugin = CompactionPlugin
