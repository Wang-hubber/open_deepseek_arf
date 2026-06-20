"""PlanSolvePlugin — task planning with dependency tracking."""
from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext
from arf.harness.adapter import PluginAdapter as _PluginAdapter
import arf.plugins.plan_solve.plugin as _mod


class PlanSolvePlugin(_NewPlugin):
    """New-style plan_solve plugin wrapping existing logic."""

    def __init__(self, name="plan_solve", events=None, config=None):
        events = events or [
            {"hook_name": "before_model", "event_name": "pre_action", "mode": "blocking"},
            {"hook_name": "before_round", "event_name": "round_start", "mode": "side"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._old = _mod.PlanSolvePlugin(self.config)
        self._adapter = _PluginAdapter(self._old)

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        await self._adapter.handle(event_name, ctx)


Plugin = PlanSolvePlugin
