"""Workspace scaffolding -- shared by CLI and API."""

import re
from pathlib import Path

import yaml

# Files created at the workspace root
WORKSPACE_TEMPLATES = {
    "arf_agent.yaml": (
        "# ARF Agent workspace configuration\n"
        "agent:\n"
        '  name: "{name}"\n'
        '  description: "Workspace managed by ARF Agent"\n'
'  model: "quick_no_thinking"\n'
        '  memory: "memory/session.md"\n'
        '  max_turns: 10\n'
        "\n"
        "resources:\n"
        "  preload: []\n"
    ),
    "memory/session.md": (
        "# Session Memory\n"
        "# This file persists conversation summaries and user preferences.\n"
        "# Maintained automatically by ARF Agent.\n"
    ),
    "memory/long_term.md": (
        "# Long-Term Memory\n"
        "# This file persists across conversations -- user profile, preferences,\n"
        "# important facts, and decisions. Max 1 MB.\n"
        "# Maintained automatically by ARF Agent.\n"
        "\n"
    ),
}


def validate_workspace_name(name: str) -> str | None:
    """Return an error message if the name is invalid, or None if valid."""
    if not name:
        return "Workspace name is required"
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        return "Name must be snake_case: lowercase letters, digits, and underscores"
    return None


def create_workspace(name: str, parent: Path) -> Path:
    """Scaffold a new workspace directory.

    Returns the workspace root path.
    """
    ws_dir = parent / name
    if ws_dir.exists():
        raise FileExistsError(f"Directory '{name}' already exists")

    ws_dir.mkdir(parents=True)

    # Create template files
    for rel_path, content in WORKSPACE_TEMPLATES.items():
        content = content.replace("{name}", name)
        file_path = ws_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    # Ensure empty resource dirs
    for sub in ("tools", "skills", "models"):
        (ws_dir / sub).mkdir(parents=True, exist_ok=True)

    return ws_dir


def copy_model_config(source_config_path: Path, dest_workspace: Path):
    """Copy model config from an existing project to a new workspace."""
    if not source_config_path.exists():
        return
    with open(source_config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    cfg = config.get("config", {})
    if cfg.get("base_url") and cfg.get("api_key") and cfg.get("model_name"):
        dest = dest_workspace / "models" / "deep_thinking" / "config.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
