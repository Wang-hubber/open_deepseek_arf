"""resource_loader -- load resource YAML from tools/ or skills/ directory."""
from pathlib import Path
import yaml


async def execute(name: str, type: str) -> dict:
    try:
        if type == "tool":
            base = Path("tools") / name
            yaml_path = base / "tool.yaml"
        elif type == "skill":
            base = Path("skills")
            yaml_path = base / f"{name}.yaml"
        else:
            return {"error": f"Unknown resource type: {type}"}

        if not yaml_path.exists():
            return {"error": f"{type} '{name}' not found"}

        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        return {"ok": True, "type": type, "name": name, "definition": data}
    except Exception as e:
        return {"error": str(e)}
