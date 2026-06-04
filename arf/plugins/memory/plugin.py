"""MemoryPlugin — periodic long-term memory extraction on round_end."""
import json
import logging
from pathlib import Path

from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.plugins.memory")


class MemoryPlugin:
    """Extracts long-term memory from session messages every N rounds.

    Writes messages to a temp file and dispatches the memory_extract tool
    to generate memory entries (stored in memory.md).
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._interval: int = cfg.get("interval", 5)
        self._max_memory_size: int = cfg.get("max_memory_size", 300)
        self._extract_on_session_end: bool = cfg.get("extract_on_session_end", False)

    @property
    def name(self) -> str:
        return "memory"

    @property
    def hooks(self) -> dict[str, str]:
        h: dict[str, str] = {"round_end": "side"}
        if self._extract_on_session_end:
            h["session_end"] = "side"
        return h

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name not in ("round_end", "session_end"):
            return

        # session_end always extracts; round_end is gated by interval
        if hook_name == "round_end":
            current_round = ctx.interaction_round
            if current_round <= 0 or current_round % self._interval != 0:
                return

        messages = ctx.state.get("messages", [])
        if not messages:
            return

        memory_dir = Path(ctx.memory_dir)
        state_dir = Path(ctx.state_dir)
        session_id = ctx.session_id
        import sys

        # Write messages to temp file for extractor
        tmp_dir = memory_dir / "state"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = tmp_dir / f"extract_{session_id}.json"
        tmp_file.write_text(json.dumps(messages, ensure_ascii=False))

        # Dispatch extractor
        extractor = (
            Path(__file__).parent
            / "tools" / "memory_extract" / "extractor.py"
        )
        import subprocess
        result = subprocess.run(
            [sys.executable, str(extractor),
             "--session-file", str(tmp_file),
             "--memory-dir", str(memory_dir),
             "--session-id", session_id],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.warning("Memory extractor failed: %s", result.stderr)
