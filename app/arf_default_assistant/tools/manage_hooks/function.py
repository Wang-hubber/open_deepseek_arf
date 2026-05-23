"""manage_hooks -- list/enable/disable hooks from config."""
from pathlib import Path
import yaml


async def execute(action: str, hook_name: str = "") -> dict:
    try:
        agent_yaml = Path("agent.yaml")
        cfg = {}
        if agent_yaml.exists():
            cfg = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}

        hooks = cfg.get("hooks", [])

        if action == "list":
            summary = []
            for h in hooks:
                summary.append({
                    "name": h.get("name"),
                    "type": h.get("type"),
                    "run": h.get("run", []),
                    "timeout": h.get("timeout", "30s"),
                })
            return {"ok": True, "hooks": summary, "count": len(summary)}

        elif action == "enable" or action == "disable":
            if not hook_name:
                return {"error": "hook_name is required for enable/disable"}
            found = False
            for h in hooks:
                if h.get("name") == hook_name:
                    found = True
                    break
            if not found:
                return {"error": f"Hook '{hook_name}' not found"}
            return {"ok": True, "action": action, "hook_name": hook_name}

        else:
            return {"error": f"Unknown action: {action}"}
    except Exception as e:
        return {"error": str(e)}
