"""TodoPlugin — task progress reminders on round_end."""
import json
import logging
from pathlib import Path

from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.plugins.todo")


class TodoPlugin:
    """Reminds the agent to update TODO when tasks haven't been touched in
    ``reminder_interval`` rounds. Only fires on round_end in execute_tools step.
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._interval: int = cfg.get("reminder_interval", 5)

    @property
    def name(self) -> str:
        return "todo"

    @property
    def hooks(self) -> dict[str, str]:
        return {"round_end": "side"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name != "round_end":
            return

        current_round = ctx.interaction_round
        if current_round <= 0:
            return

        tasks_file = Path(ctx.workspace_dir) / "tasks.json"
        if not tasks_file.exists():
            return

        try:
            data = json.loads(tasks_file.read_text())
            last_updated = data.get("last_updated_round", 0)
        except (json.JSONDecodeError, OSError):
            return

        rounds_since = current_round - last_updated
        if rounds_since >= self._interval:
            ctx.state.setdefault("messages", []).append({
                "role": "system",
                "content": (
                    f"[TODO Reminder] {rounds_since} rounds since last TODO update. "
                    f"Please check task progress and call todo tool to sync."
                ),
            })
