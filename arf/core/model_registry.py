"""ModelRegistry — validate and resolve model references from agent.yaml.

Parses the top-level ``models`` list in agent.yaml, validates all downstream
references, and resolves model configs with partial-override merge support.
"""
from dataclasses import dataclass, field


@dataclass
class ResolvedModelConfig:
    """A fully resolved model configuration ready for ModelAdapter consumption."""
    model: str
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    kwargs: dict = field(default_factory=dict)


class ModelRegistry:
    """Parses the ``models`` definition list from agent.yaml.

    Each entry is a dict: ``{model, api_base, api_key_env, kwargs}``.
    The ``model`` field is the unique identifier.

    Usage::

        registry = ModelRegistry(raw_models)
        registry.validate()

        cfg = registry.resolve("deepseek-v4-pro")
        cfgs = registry.resolve_list([
            {"model": "deepseek-v4-pro"},
            {"model": "flash", "kwargs": {"temperature": 0}},
        ])
    """

    def __init__(self, raw_models: list[dict]) -> None:
        self._defs: dict[str, ResolvedModelConfig] = {}
        for raw in raw_models:
            cfg = ResolvedModelConfig(
                model=raw["model"],
                api_base=raw.get("api_base", "https://api.deepseek.com"),
                api_key_env=raw.get("api_key_env", "DEEPSEEK_API_KEY"),
                kwargs=raw.get("kwargs", {}),
            )
            self._defs[cfg.model] = cfg

    def validate(self) -> None:
        """Validate model definitions. Currently a no-op (dict keys enforce uniqueness).

        Raises:
            ValueError: if any model config is invalid.
        """
        for name, cfg in self._defs.items():
            if not cfg.api_key_env:
                raise ValueError(
                    f"Model '{name}': api_key_env must not be empty"
                )

    def has(self, model_name: str) -> bool:
        """Check if a model name is defined."""
        return model_name in self._defs

    def resolve(self, model_name: str) -> ResolvedModelConfig:
        """Resolve a single model name to its full config.

        Raises:
            KeyError: if the model name is not defined.
        """
        if model_name not in self._defs:
            raise KeyError(
                f"Model '{model_name}' not found in models definitions. "
                f"Defined models: {list(self._defs.keys())}"
            )
        return self._defs[model_name]

    def resolve_list(self, refs: list[dict]) -> list[ResolvedModelConfig]:
        """Resolve a list of model references with partial-override merge.

        Each ref is a dict with at minimum ``{"model": "name"}``.
        Additional keys (api_base, api_key_env, kwargs) override the definition.

        Returns a list of fully resolved configs in the same order.
        """
        result = []
        for ref in refs:
            name = ref.get("model", "")
            if not name:
                raise ValueError(f"Model reference missing 'model' key: {ref}")
            base = self.resolve(name)
            overrides = {k: v for k, v in ref.items() if k != "model"}
            if overrides:
                merged = ResolvedModelConfig(
                    model=base.model,
                    api_base=overrides.get("api_base", base.api_base),
                    api_key_env=overrides.get("api_key_env", base.api_key_env),
                    kwargs={**base.kwargs, **overrides.get("kwargs", {})},
                )
                result.append(merged)
            else:
                result.append(base)
        return result

    def list_names(self) -> list[str]:
        """Return all defined model names."""
        return list(self._defs.keys())
