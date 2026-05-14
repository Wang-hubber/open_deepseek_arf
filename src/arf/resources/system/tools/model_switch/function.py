"""model_switch -- runtime model switching with degradation fallback."""

from pathlib import Path
import yaml

AGENT_CONFIG_FILE = "arf_agent.yaml"

# Degradation map: if target is unavailable, try the next level up
DEGRADATION = {
    "quick_no_thinking": ["quick_no_thinking", "quick_thinking", "deep_thinking"],
    "quick_thinking": ["quick_thinking", "deep_thinking"],
    "deep_thinking": ["deep_thinking"],
}


def _find_model_by_type(models_dir: Path, model_type: str) -> str | None:
    """Find a configured model name matching the given model_type."""
    if not models_dir.exists():
        return None
    for sub in sorted(models_dir.iterdir()):
        if not sub.is_dir():
            continue
        cfg_file = sub / "config.yaml"
        if not cfg_file.exists():
            continue
        try:
            cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if cfg.get("model_type") != model_type:
            continue
        c = cfg.get("config", {})
        if c.get("base_url") and c.get("api_key") and c.get("model_name"):
            return sub.name
    return None


def execute(target: str, _workspace_dir: str | None = None, **kwargs) -> dict:
    """Switch the active model to the specified target level.

    Args:
        target: One of quick_no_thinking, quick_thinking, deep_thinking.
        _workspace_dir: Injected by the agent (workspace root path).

    Returns:
        {"ok": True, "switched_to": name, "model_type": type, "degraded": bool}
    """
    if _workspace_dir is None:
        return {"error": "Workspace directory not available"}

    ws = Path(_workspace_dir)
    if not ws.exists():
        return {"error": f"Workspace directory not found: {_workspace_dir}"}

    models_dir = ws / "models"

    # Try degradation chain
    for attempt_type in DEGRADATION.get(target, [target]):
        name = _find_model_by_type(models_dir, attempt_type)
        if name is not None:
            # Update arf_agent.yaml
            agent_yaml = ws / AGENT_CONFIG_FILE
            cfg = {}
            if agent_yaml.exists():
                try:
                    cfg = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
                except Exception:
                    cfg = {}
            cfg.setdefault("agent", {})["model"] = name
            agent_yaml.write_text(
                yaml.safe_dump(cfg, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )

            degraded = attempt_type != target
            return {
                "ok": True,
                "switched_to": name,
                "model_type": attempt_type,
                "degraded": degraded,
                "message": (
                    f"Switched to {name} ({attempt_type})."
                    if not degraded
                    else f"Target '{target}' unavailable, degraded to {name} ({attempt_type})."
                ),
            }

    return {
        "error": (
            f"No model available for '{target}' or any fallback level. "
            "Please configure at least one model."
        ),
    }
