"""Fast model helpers -- shared by routes and WebSocket handler.

Searches the registry for a quick_thinking model (prefers one named "fast"),
returns ModelAdapter or a full ARFAgent for utility tasks like title
generation and memory extraction.
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


def make_fast_agent(registry: ResourceRegistry, workspace_dir: str) -> object | None:
    """Create an ARFAgent backed by a quick_thinking model.
    Returns None if no quick_thinking model is configured."""
    model = load_fast_model(registry)
    if model is None:
        return None
    from arf.agent import ARFAgent
    return ARFAgent(model, registry, workspace_dir)


def is_fast_model_configured(registry: ResourceRegistry) -> bool:
    """Check if any quick_thinking model is configured."""
    return load_fast_model(registry) is not None
