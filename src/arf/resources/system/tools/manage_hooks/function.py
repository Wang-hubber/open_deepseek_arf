"""manage_hooks tool -- read/write .hooks.json in the workspace.

This tool directly manipulates the hook configuration file so the agent
can register, modify, or remove hooks on behalf of the user without
needing to call an HTTP API.

Hook events:
  SessionStart  -- fires once after the first assistant response
  PreModelCall  -- fires before each LLM call
  PostModelCall -- fires after each LLM call
  PreToolUse    -- fires before each tool execution
  PostToolUse   -- fires after each tool execution
  SessionEnd    -- fires when the conversation ends (disconnect/timeout)

Exit-code contract (for hook commands):
  0 -- continue (stdout JSON may carry extra data)
  1 -- block current action (stderr = reason)
  2 -- inject a message (stderr = message text)
"""

import json
import os
from pathlib import Path

HOOK_EVENTS = ("SessionStart", "PreModelCall", "PostModelCall", "PreToolUse", "PostToolUse", "SessionEnd")
DEFAULT_TIMEOUT = 30


def execute(
    action: str,
    _workspace_dir: str = "",
    event: str = "",
    name: str = "",
    command: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    enabled: bool = True,
    matcher: str = "",
    **kwargs,
) -> dict:
    """Manage hook rules in the workspace .hooks.json.

    Args:
        action: list | add | remove | update
        _workspace_dir: injected workspace path
        event: hook event name
        name: hook name (unique identifier)
        command: shell command string
        timeout: timeout in seconds
        enabled: whether the hook is active
        matcher: tool name filter (PreToolUse/PostToolUse only)
    """
    ws = Path(_workspace_dir) if _workspace_dir else Path.cwd()
    config_path = ws / ".hooks.json"

    if action == "list":
        return _list_hooks(config_path)

    if action == "add":
        if not event or not name or not command:
            return {"ok": False, "error": "event, name, and command are required for 'add'"}
        return _add_hook(config_path, event, name, command, timeout, enabled, matcher)

    if action == "remove":
        if not event or not name:
            return {"ok": False, "error": "event and name are required for 'remove'"}
        return _remove_hook(config_path, event, name)

    if action == "update":
        if not event or not name:
            return {"ok": False, "error": "event and name are required for 'update'"}
        updates = {}
        if command:
            updates["command"] = command
        if timeout != DEFAULT_TIMEOUT:
            updates["timeout"] = timeout
        if not enabled:
            updates["enabled"] = enabled
        if matcher:
            updates["matcher"] = matcher
        return _update_hook(config_path, event, name, updates)

    return {"ok": False, "error": f"Unknown action: {action}"}


def _load_config(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            pass
    return {"version": 1, "hooks": {e: [] for e in HOOK_EVENTS}}


def _save_config(path: Path, config: dict) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_hooks(path: Path) -> dict:
    config = _load_config(path)
    hooks = config.get("hooks", {})
    # Build a readable summary
    summary = {}
    for event in HOOK_EVENTS:
        items = []
        for h in hooks.get(event, []):
            items.append({
                "name": h.get("name", ""),
                "command": h.get("command", ""),
                "timeout": h.get("timeout", DEFAULT_TIMEOUT),
                "enabled": h.get("enabled", True),
                "matcher": h.get("matcher"),
            })
        summary[event] = items
    return {"ok": True, "hooks": summary}


def _add_hook(
    path: Path, event: str, name: str, command: str,
    timeout: int, enabled: bool, matcher: str,
) -> dict:
    if event not in HOOK_EVENTS:
        return {"ok": False, "error": f"Invalid event: {event}. Must be one of {HOOK_EVENTS}"}

    config = _load_config(path)
    hooks = config.setdefault("hooks", {})
    hooks.setdefault(event, [])

    # Check for duplicate name in this event
    for h in hooks[event]:
        if h.get("name") == name:
            return {"ok": False, "error": f"Hook '{name}' already exists in {event}. Use 'update' to modify it."}

    entry = {
        "name": name,
        "command": command,
        "timeout": timeout,
        "enabled": enabled,
    }
    if matcher:
        entry["matcher"] = matcher

    hooks[event].append(entry)
    _save_config(path, config)
    return {"ok": True, "message": f"Hook '{name}' added to {event}"}


def _remove_hook(path: Path, event: str, name: str) -> dict:
    if event not in HOOK_EVENTS:
        return {"ok": False, "error": f"Invalid event: {event}"}

    config = _load_config(path)
    hooks = config.get("hooks", {}).get(event, [])
    before = len(hooks)
    config["hooks"][event] = [h for h in hooks if h.get("name") != name]

    if len(config["hooks"][event]) == before:
        return {"ok": False, "error": f"Hook '{name}' not found in {event}"}

    _save_config(path, config)
    return {"ok": True, "message": f"Hook '{name}' removed from {event}"}


def _update_hook(path: Path, event: str, name: str, updates: dict) -> dict:
    if event not in HOOK_EVENTS:
        return {"ok": False, "error": f"Invalid event: {event}"}

    config = _load_config(path)
    hooks = config.get("hooks", {}).get(event, [])

    for h in hooks:
        if h.get("name") == name:
            for key, value in updates.items():
                h[key] = value
            _save_config(path, config)
            return {"ok": True, "message": f"Hook '{name}' updated in {event}"}

    return {"ok": False, "error": f"Hook '{name}' not found in {event}"}
