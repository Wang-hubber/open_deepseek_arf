"""Model manager tool -- CRUD + test + switch for model configs."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

MODELS_SUBDIR = "models"
AGENT_CONFIG_FILE = "arf_agent.yaml"


def execute(
    action: str,
    name: str = "",
    model_type: str = "",
    base_url: str = "",
    api_key: str = "",
    model_name: str = "",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    _workspace_dir: str = "",
) -> dict:
    base_dir = _resolve_models_dir(_workspace_dir)

    handlers = {
        "create": _handle_create,
        "list": _handle_list,
        "get": _handle_get,
        "update": _handle_update,
        "delete": _handle_delete,
        "test": _handle_test,
        "switch": _handle_switch,
    }
    handler = handlers.get(action)
    if not handler:
        return {"error": f"Unknown action: {action}"}

    if action == "list":
        return handler(base_dir)
    if not name:
        return {"error": "name is required for this action"}
    return handler(base_dir, name, model_type, base_url, api_key, model_name,
                   temperature, max_tokens, _workspace_dir)


def _resolve_models_dir(workspace_dir: str) -> Path:
    if workspace_dir:
        base = Path(workspace_dir) / MODELS_SUBDIR
    else:
        base = Path.cwd() / MODELS_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def _config_path(models_dir: Path, name: str) -> Path:
    return models_dir / name / "config.yaml"


def _read_config(filepath: Path) -> dict:
    if not filepath.exists():
        return {}
    return yaml.safe_load(filepath.read_text(encoding="utf-8")) or {}


def _handle_create(models_dir, name, model_type, base_url, api_key, model_name,
                   temperature, max_tokens, _workspace_dir) -> dict:
    if not base_url or not api_key or not model_name:
        return {"error": "base_url, api_key, and model_name are required for create"}
    if not model_type:
        return {"error": "model_type is required for create"}

    config_dir = models_dir / name
    config_file = config_dir / "config.yaml"
    if config_file.exists():
        return {"error": f"Model '{name}' already exists. Use update to modify it."}

    config = {
        "name": name,
        "model_type": model_type,
        "config": {
            "base_url": base_url,
            "api_key": api_key,
            "model_name": model_name,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    }
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        yaml.safe_dump(config, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    return {"ok": True, "name": name, "path": str(config_file)}


def _handle_list(models_dir, **_kw) -> dict:
    models = []
    for sub in sorted(models_dir.iterdir()):
        if not sub.is_dir():
            continue
        cfg_file = sub / "config.yaml"
        cfg = _read_config(cfg_file)
        if not cfg:
            continue
        c = cfg.get("config", {})
        models.append({
            "name": cfg.get("name", sub.name),
            "model_type": cfg.get("model_type", ""),
            "base_url": c.get("base_url", ""),
            "model_name": c.get("model_name", ""),
            "active": _is_active(sub.name, models_dir.parent),
        })
    return {"models": models, "count": len(models)}


def _handle_get(models_dir, name, *args, **kw) -> dict:
    cfg_file = _config_path(models_dir, name)
    cfg = _read_config(cfg_file)
    if not cfg:
        return {"error": f"Model '{name}' not found"}
    return {"ok": True, "config": cfg, "active": _is_active(name, models_dir.parent)}


def _handle_update(models_dir, name, model_type, base_url, api_key, model_name,
                   temperature, max_tokens, _workspace_dir) -> dict:
    cfg_file = _config_path(models_dir, name)
    cfg = _read_config(cfg_file)
    if not cfg:
        return {"error": f"Model '{name}' not found. Use create to add it."}

    if model_type:
        cfg["model_type"] = model_type
    c = cfg.setdefault("config", {})
    if base_url:
        c["base_url"] = base_url
    if api_key:
        c["api_key"] = api_key
    if model_name:
        c["model_name"] = model_name
    if temperature is not None:
        c["temperature"] = temperature
    if max_tokens is not None:
        c["max_tokens"] = max_tokens

    cfg_file.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    return {"ok": True, "name": name}


def _handle_delete(models_dir, name, *args, **kw) -> dict:
    cfg_file = _config_path(models_dir, name)
    if not cfg_file.exists():
        return {"error": f"Model '{name}' not found"}
    deleted = cfg_file.with_suffix(".yaml_deleted")
    cfg_file.rename(deleted)
    return {"ok": True, "name": name}


def _handle_test(models_dir, name, *args, **kw) -> dict:
    cfg_file = _config_path(models_dir, name)
    cfg = _read_config(cfg_file)
    if not cfg:
        return {"error": f"Model '{name}' not found"}
    c = cfg.get("config", {})
    if not c.get("base_url") or not c.get("api_key"):
        return {"error": f"Model '{name}' has incomplete config (missing base_url or api_key)"}

    from arf.resources.model_adapter import ModelAdapter

    try:
        t0 = time.time()
        adapter = ModelAdapter(c)
        response = adapter.chat([{"role": "user", "content": "hello"}])
        latency_ms = int((time.time() - t0) * 1000)
        return {"ok": True, "response": response, "latency_ms": latency_ms}
    except Exception as e:
        return {"error": f"Connection test failed: {e}"}


def _handle_switch(models_dir, name, *args, **kw) -> dict:
    cfg_file = _config_path(models_dir, name)
    if not cfg_file.exists():
        return {"error": f"Model '{name}' not found"}
    workspace_dir = str(models_dir.parent)
    agent_yaml = Path(workspace_dir) / AGENT_CONFIG_FILE
    if not agent_yaml.exists():
        return {"error": f"Workspace config not found: {agent_yaml}"}

    cfg = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
    cfg.setdefault("agent", {})["model"] = name
    agent_yaml.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    return {"ok": True, "active_model": name, "restart_required": False}


def _is_active(name: str, workspace_dir: Path) -> bool:
    agent_yaml = workspace_dir / AGENT_CONFIG_FILE
    if not agent_yaml.exists():
        return False
    try:
        cfg = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
        return cfg.get("agent", {}).get("model") == name
    except Exception:
        return False
