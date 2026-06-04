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

    @property
    def name(self) -> str:
        return "memory"

    @property
    def hooks(self) -> dict[str, str]:
        return {"round_end": "side"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name != "round_end":
            return

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
