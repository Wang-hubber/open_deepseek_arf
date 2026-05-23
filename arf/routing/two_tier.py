"""TwoTierRouter — complexity classifier -> model selection."""
from arf.core.config_base import RoutingConfig


class TwoTierRouter:
    def __init__(self, config: RoutingConfig, models: list[str], classifier_call=None) -> None:
        self._cfg = config
        self._models = models
        self._classify = classifier_call

    async def route(self, query: str, history: list[dict]) -> str:
        level = await self.classify(query)
        return self._cfg.classify.get(level, self._cfg.default)

    async def classify(self, query: str) -> str:
        if self._classify:
            return await self._classify(query)
        return "medium"

    def fallback_from(self, model_name: str) -> str | None:
        return self._cfg.fallback.get(model_name)
