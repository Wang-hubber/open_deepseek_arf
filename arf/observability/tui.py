"""TuiDashboard — Rich terminal real-time debug panel."""
import os
from arf.core.events import AgentEvent


class TuiDashboard:
    def __init__(self) -> None:
        self._enabled = os.environ.get("ARF_TUI", "0") == "1"
        self._stats: dict[str, dict] = {}
        self._timeline: list[tuple] = []

    async def consume(self, events: list[AgentEvent]) -> None:
        if not self._enabled:
            return
        for e in events:
            if e.type == "model_call_end":
                model = e.data.get("model", "unknown")
                m = self._stats.setdefault(model, {"calls": 0, "tokens_in": 0, "tokens_out": 0})
                m["calls"] += 1
                m["tokens_in"] += e.data.get("tokens_in", 0)
                m["tokens_out"] += e.data.get("tokens_out", 0)
            elif e.type == "tool_call_end":
                self._timeline.append((e.timestamp, e.data.get("tool_name", ""),
                                       e.data.get("duration_ms", 0)))

    def render(self) -> str:
        lines = ["ARF Agent Dashboard", "=" * 40]
        for model, stats in self._stats.items():
            lines.append(f"  {model}: {stats['calls']} calls, "
                         f"{stats['tokens_in']} in / {stats['tokens_out']} out")
        lines.append(f"\nTool Timeline ({len(self._timeline)} calls):")
        for ts, name, dur in self._timeline[-10:]:
            lines.append(f"  {name}: {dur:.0f}ms")
        return "\n".join(lines)
