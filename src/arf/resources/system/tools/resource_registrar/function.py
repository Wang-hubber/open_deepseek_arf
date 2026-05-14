"""resource_registrar -- query and request configuration for system resources.

Injected state (provided by engine at call time):
    _registry: ResourceRegistry instance
"""

import traceback
from datetime import datetime


def _log(level: str, message: str, **extra):
    """Write structured log to stderr."""
    import json
    import sys
    log_entry = {
        "tool": "resource_registrar",
        "level": level,
        "message": message,
        "timestamp": datetime.now().isoformat(),
        **extra,
    }
    print(json.dumps(log_entry, ensure_ascii=False), file=sys.stderr)


def execute(action, resource_type="", name="", _registry=None) -> dict:
    try:
        if not _registry:
            return {"error": "Registry not available", "detail": "RuntimeError"}

        if action == "check":
            if not resource_type or not name:
                return {"error": "resource_type and name required for 'check' action"}
            rtype = resource_type + "s"  # "model" -> "models"
            item = _registry.get(rtype, name)
            if not item:
                return {"ok": True, "configured": False, "found": False,
                        "hint": f"Resource '{rtype}/{name}' is not a known system slot"}
            if item.get("configured", False):
                return {"ok": True, "configured": True, "found": True}
            return {
                "ok": True,
                "configured": False,
                "found": True,
                "name": name,
                "resource_type": resource_type,
                "description": item.get("description", ""),
                "config_template": item.get("config_template", {}),
                "depends_on": item.get("depends_on", []),
                "required": item.get("required", False),
            }

        elif action == "list_pending":
            unconfigured = _registry.list_unconfigured()
            return {"ok": True, "pending": unconfigured, "count": len(unconfigured)}

        elif action == "check_deps":
            if not resource_type or not name:
                return {"error": "resource_type and name required for 'check_deps' action"}
            rtype = resource_type + "s"
            result = _registry.check_deps(rtype, name)
            return {"ok": True, **result}

        else:
            return {"error": f"Unknown action: {action}", "detail": "ValueError"}

    except Exception as exc:
        _log("error", str(exc), traceback=traceback.format_exc())
        return {"error": str(exc), "detail": type(exc).__name__}
