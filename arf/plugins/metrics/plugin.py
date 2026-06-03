"""MetricsPlugin — collect token usage and latency metrics."""
import time
from arf.core.plugin_context import PluginContext


class MetricsPlugin:
    def __init__(self, config: dict | None = None):
        self._metrics: dict[str, list] = {}

    @property
    def name(self) -> str:
        return "metrics"

    @property
    def hooks(self) -> dict[str, str]:
        return {
            "turn_start": "side", "post_dispatch": "side",
            "turn_end": "side", "session_end": "side",
        }

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "turn_start":
            ctx.hook_data["_metric_turn_start"] = time.time()
        elif hook_name == "post_dispatch":
            start = ctx.hook_data.get("_metric_turn_start", time.time())
            duration = time.time() - start
            ctx.state.setdefault("_metrics", []).append({
                "step": ctx.current_step, "duration_ms": duration * 1000,
                "turn": ctx.turn,
            })
