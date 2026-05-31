"""TwoTierRouter — complexity classifier -> model selection."""

# ---- keyword heuristic (E2E Bug 3.4) ----

COMPLEX_KEYWORDS = [
    "create", "build", "generate", "implement",
    "refactor", "rewrite", "debug", "deploy",
    "design", "architect",
]

MEDIUM_KEYWORDS = [
    "read", "list", "show", "display", "find", "search",
    "what is", "who is", "explain", "describe", "summarize",
    "how many", "how do", "summary",
]


def keyword_classify(query: str) -> str | None:
    """Fast keyword heuristic for task complexity classification.

    Returns 'medium', 'complex', or None (ambiguous — needs LLM).
    Case-insensitive.
    """
    q = query.lower()
    has_complex = any(kw in q for kw in COMPLEX_KEYWORDS)
    has_medium = any(kw in q for kw in MEDIUM_KEYWORDS)
    if has_complex and not has_medium:
        return "complex"
    if has_medium and not has_complex:
        return "medium"
    return None  # both or neither — ambiguous


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
