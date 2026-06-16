"""CompactionPlugin — structured context compaction with boundary markers.

Follows Claude Code compaction protocol:
  - Inserts compact_boundary system message + isCompactSummary user message
  - Emits compaction_start / compaction_end events with full metadata
  - Supports auto (threshold) and manual (tool-call) triggers
  - Full history preserved in trace; state holds active-context messages only
"""

import logging

from arf.core.plugin_context import PluginContext
from arf.core.state import AgentState
from arf.plugins.compaction.summarizer import summarize

logger = logging.getLogger("arf.plugins.compaction")

DEFAULT_WINDOW_SIZE = 131_072


class CompactionPlugin:
    """Structured context compaction mounted on round_end hook.

    When token usage exceeds threshold * window_size, old messages are
    summarized via LLM and replaced with a compact_boundary marker +
    summary message. The full history is preserved in the trace JSONL.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}

        # L3 LLM compaction
        self.threshold: float = cfg.get("threshold", 0.75)
        self.window_size: int = cfg.get("window_size", 131_072)
        self.keep_count: int = cfg.get("keep_count", 8)

        # Truncation (L1/L2 + safeguard)
        tr_cfg = cfg.get("truncation", {})
        self.l1_threshold: float = tr_cfg.get("l1_threshold", 0.50)
        self.l2_threshold: float = tr_cfg.get("l2_threshold", 0.70)
        self._preview_chars: int = tr_cfg.get("preview_chars", 100)
        self._window_ratio: float = tr_cfg.get("window_ratio", 0.15)

        # Infrastructure (injected)
        self._model_context_window: int | None = None
        self._call_model = None
        self._state_store = None
        self._data_dir: str = cfg.get("data_dir", "./data")

        # Per-session state
        self._level: dict[str, int] = {}              # 0=clean, 1=L1, 2=L2
        self._cooldown: dict[str, int] = {}
        self._compaction_count: dict[str, int] = {}
        self._truncation_count: dict[str, int] = {}
        self._avg_tools_per_round: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "compaction"

    @property
    def hooks(self) -> dict[str, str]:
        return {
            "round_end": "blocking",
            "tool_output": "blocking",
        }

    def set_call_model(self, call_model) -> None:
        self._call_model = call_model

    def set_state_store(self, state_store) -> None:
        self._state_store = state_store

    def set_model_context_window(self, context_window: int) -> None:
        """Inject model context window size (from ModelConfig.context_window).
        Takes precedence over plugin.yaml window_size.
        """
        self._model_context_window = context_window

    def set_data_dir(self, data_dir: str) -> None:
        self._data_dir = data_dir

    @property
    def effective_window_size(self) -> int:
        """Model context window if injected, otherwise plugin config fallback."""
        return self._model_context_window or self.window_size

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "round_end":
            await self._maybe_truncate_or_compact(ctx)
        elif hook_name == "tool_output":
            await self._safeguard(ctx)

    async def _safeguard(self, ctx: PluginContext) -> None:
        """Dynamic-threshold safeguard: truncate single oversized tool results.

        Only fires for extreme outputs that would skip multiple compaction levels.
        Threshold = window * window_ratio / avg_tools_per_round, floored at 500 chars.
        """
        import hashlib
        from pathlib import Path

        raw_results = ctx.hook_data.get("_raw_tool_results", {})
        session_id = ctx.session_id
        turn = ctx.turn or 0

        threshold = (self.effective_window_size * self._window_ratio) / max(
            self._avg_tools_per_round.get(session_id, 1.0), 1.0
        )
        threshold = max(int(threshold), 500)

        for tc_id, r in raw_results.items():
            result_data = r.get("data", "")
            if not result_data or len(result_data) <= threshold:
                continue

            tool_name = r.get("tool_name", "")
            content_hash = hashlib.sha1(result_data.encode()).hexdigest()[:8]
            output_dir = Path(self._data_dir) / session_id / "tool_outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"turn_{turn}_{tool_name}_{content_hash}.txt"

            try:
                output_path.write_text(result_data, encoding="utf-8")
            except OSError:
                logger.warning("Safeguard: failed to write tool output to %s", output_path)
                continue

            preview = result_data[:self._preview_chars]
            new_data = (
                f"[Tool output truncated — {len(result_data)} chars, full at {output_path}]\n"
                f"{preview}..."
            )
            r["data"] = new_data
            logger.debug(
                "Safeguard: truncated %s output (%d chars) session=%s turn=%d",
                tool_name, len(result_data), session_id, turn,
            )

    async def compact_now(self, ctx: PluginContext, trigger: str = "manual") -> dict:
        """Public API for manual compaction via tool call.

        Returns metadata dict suitable for tool response.
        """
        return await self._compact(ctx, trigger)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _maybe_truncate_or_compact(self, ctx: PluginContext) -> None:
        """Progressive compaction state machine (full impl in later tasks).

        For now: maintain EMA, then delegate to existing _maybe_compact logic.
        """
        sid = ctx.session_id
        # Maintain EMA for safeguard
        raw = ctx.hook_data.get("_raw_tool_results", {})
        actual_count = len(raw)
        prev_avg = self._avg_tools_per_round.get(sid, 1.0)
        self._avg_tools_per_round[sid] = 0.7 * prev_avg + 0.3 * actual_count

        # Delegate to existing compaction for now
        await self._maybe_compact(ctx)

    async def _maybe_compact(self, ctx: PluginContext) -> None:
        sid = ctx.session_id
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
        limit = int(self.threshold * self.effective_window_size)
        if last_usage <= limit:
            return

        await self._compact(ctx, "auto")

    async def _compact(self, ctx: PluginContext, trigger: str) -> dict:
        sid = ctx.session_id
        if not self._state_store:
            return {"ok": False, "error": "No state_store available"}

        state: AgentState = await self._state_store.get(sid)
        if not state:
            return {"ok": False, "error": "No state found"}

        msgs = state.get("messages", [])
        last_usage = state.get("last_token_usage", 0)
        round_num = state.get("interaction_round", ctx.interaction_round)

        # Find split point: keep last N non-tool messages
        ua_indices = [
            i for i, m in enumerate(msgs)
            if isinstance(m, dict) and m.get("role") != "tool"
        ]
        if len(ua_indices) <= self.keep_count:
            return {"ok": True, "compacted": 0, "reason": "below_keep_count"}

        split = ua_indices[-self.keep_count]
        old_msgs = msgs[:split]
        recent_msgs = msgs[split:]

        # Emit compaction_start event
        ctx.inject_engine_event("compaction_start", {
            "trigger": trigger,
            "pre_tokens": last_usage,
            "total_messages": len(msgs),
            "compacting_count": len(old_msgs),
            "keeping_count": len(recent_msgs),
            "round": round_num,
        })

        # Generate summary
        existing = state.get("context_summary", "")
        summary_text = ""
        if self._call_model:
            try:
                summary_text = await summarize(
                    self._call_model, old_msgs, existing_summary=existing
                )
            except Exception:
                logger.exception("Compaction summarizer failed, using fallback")
                summary_text = existing + "\n(compaction failed — truncated)"

        # Build boundary marker + summary messages (Claude Code protocol)
        compact_boundary = {
            "role": "system",
            "content": "",
            "subtype": "compact_boundary",
            "compactMetadata": {
                "trigger": trigger,
                "preTokens": last_usage,
                "compactedCount": len(old_msgs),
                "summaryLength": len(summary_text),
                "round": round_num,
            },
        }
        compact_summary = {
            "role": "user",
            "content": summary_text,
            "isCompactSummary": True,
        }

        # Replace old messages with boundary + summary in state
        new_msgs = [compact_boundary, compact_summary] + recent_msgs
        new_state = {**state, "messages": new_msgs, "context_summary": summary_text}

        await self._state_store.put(sid, new_state)

        # Track stats
        count = self._compaction_count.get(sid, 0) + 1
        self._compaction_count[sid] = count
        self._cooldown[sid] = 2

        # Emit compaction_end event
        ctx.inject_engine_event("compaction_end", {
            "trigger": trigger,
            "compacted_count": len(old_msgs),
            "kept_count": len(recent_msgs),
            "summary_length": len(summary_text),
            "total_compactions": count,
            "round": round_num,
        })

        logger.info(
            "Compaction #%d (%s): %d msgs compacted → %d chars summary, %d msgs kept (session=%s)",
            count, trigger, len(old_msgs), len(summary_text), len(recent_msgs), sid,
        )

        return {
            "ok": True,
            "compacted": len(old_msgs),
            "kept": len(recent_msgs),
            "summary_length": len(summary_text),
            "total_compactions": count,
            "trigger": trigger,
        }
