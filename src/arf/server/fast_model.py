"""Fast model helpers -- shared by routes and WebSocket handler.

Searches the registry for a quick_thinking model (prefers one named "fast"),
returns ModelAdapter for utility tasks like title generation and
memory extraction.
"""

from __future__ import annotations

from arf.resources.manager import ResourceRegistry
from arf.resources.model_adapter import ModelAdapter


def load_fast_model(registry: ResourceRegistry) -> ModelAdapter | None:
    """Load a quick_thinking model from the registry.
    Prefers the model named 'fast', falls back to any quick_thinking.
    Returns ModelAdapter if configured, None otherwise."""
    for name, m in registry._items.get("models", {}).items():
        if m.get("model_type") != "quick_thinking":
            continue
        cfg = m.get("config", {})
        if cfg.get("base_url") and cfg.get("api_key") and cfg.get("model_name"):
            return ModelAdapter(cfg)
    return None




def is_fast_model_configured(registry: ResourceRegistry) -> bool:
    """Check if any quick_thinking model is configured."""
    return load_fast_model(registry) is not None
