"""resource_registrar -- list resources from the config directory."""
from pathlib import Path
import yaml


async def execute(action: str, resource_type: str = "", name: str = "") -> dict:
    try:
        if action == "check":
            if not resource_type or not name:
                return {"error": "resource_type and name required for 'check' action"}
            dir_name = resource_type + "s"
            if resource_type == "tool":
                found = (Path("tools") / name / "tool.yaml").exists()
            elif resource_type == "skill":
                found = (Path("skills") / f"{name}.yaml").exists()
            else:
                found = False
            return {"ok": True, "configured": found, "found": found, "name": name, "resource_type": resource_type}

        elif action == "list_pending":
            unconfigured = []
            return {"ok": True, "pending": unconfigured, "count": 0}

        elif action == "check_deps":
            if not resource_type or not name:
                return {"error": "resource_type and name required for 'check_deps' action"}
            return {"ok": True, "missing": []}

        else:
            return {"error": f"Unknown action: {action}"}
    except Exception as e:
        return {"error": str(e), "detail": type(e).__name__}
