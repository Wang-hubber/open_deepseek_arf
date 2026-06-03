"""StrategyPlugin — select loop strategy at round_start."""
from arf.core.plugin_context import PluginContext


class StrategyPlugin:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._default = cfg.get("default_strategy", "react")
        self._strategies: dict = {}

    def register(self, name: str, strategy) -> None:
        self._strategies[name] = strategy

    @property
    def name(self) -> str:
        return "strategy"

    @property
    def hooks(self) -> dict[str, str]:
        return {"round_start": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        override = ctx.state.get("override_strategy")
        strategy_name = override or self._default
        if strategy_name in self._strategies:
            ctx.hook_data["strategy"] = self._strategies[strategy_name]
