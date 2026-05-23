"""model_switch -- switch the active model at runtime."""
import yaml
from pathlib import Path


async def execute(model: str) -> dict:
    try:
        agent_yaml = Path("agent.yaml")
        if agent_yaml.exists():
            cfg = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
            models = cfg.get("models", [])
            model_names = [m.get("name") for m in models]
            if model not in model_names:
                available = ", ".join(model_names)
                return {"error": f"Model '{model}' not found. Available: {available}"}
        return {"model_switched": model}
    except Exception as e:
        return {"error": str(e)}
