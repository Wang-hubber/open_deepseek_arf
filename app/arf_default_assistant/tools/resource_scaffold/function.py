"""resource_scaffold -- create directory skeleton for a new tool or skill."""
from pathlib import Path


async def execute(type: str, name: str, description: str = "") -> dict:
    try:
        if type == "tool":
            base = Path("tools") / name
            base.mkdir(parents=True, exist_ok=True)
            tool_yaml = base / "tool.yaml"
            if not tool_yaml.exists():
                tool_yaml.write_text(
                    f"name: {name}\n"
                    f"description: {description or 'New tool'}\n"
                    f"parameters:\n"
                    f"  type: object\n"
                    f"  properties:\n"
                    f"    input:\n"
                    f"      type: string\n"
                    f"      description: Input parameter\n"
                    f"  required:\n"
                    f"    - input\n"
                    f"execution:\n"
                    f"  sandbox: inherit\n"
                    f"  timeout: 30s\n"
                    f"activation: discoverable\n",
                    encoding="utf-8",
                )
            func_py = base / "function.py"
            if not func_py.exists():
                func_py.write_text(
                    f'"""execute function for {name}."""\n\n\n'
                    f"async def execute(input: str) -> dict:\n"
                    f'    """Implement {description or name} logic."""\n'
                    f"    return {{'ok': True, 'result': f'Processed: {{input}}'}}\n",
                    encoding="utf-8",
                )
            return {"ok": True, "type": "tool", "name": name, "path": str(base)}

        elif type == "skill":
            base = Path("skills")
            base.mkdir(parents=True, exist_ok=True)
            skill_path = base / f"{name}.yaml"
            if not skill_path.exists():
                skill_path.write_text(
                    f"name: {name}\n"
                    f"description: {description or 'New skill'}\n"
                    f"prompt: |\n"
                    f"  You are using the {name} skill.\n"
                    f"  {description or 'Perform the task as described.'}\n"
                    f"tools: []\n"
                    f"activation: discoverable\n",
                    encoding="utf-8",
                )
            return {"ok": True, "type": "skill", "name": name, "path": str(skill_path)}

        else:
            return {"error": f"Unknown resource type: {type}"}
    except Exception as e:
        return {"error": str(e)}


async def rollback(type: str, name: str, description: str = "") -> dict:
    """Undo resource_scaffold: delete the created tool or skill directory."""
    import shutil
    try:
        if type == "tool":
            base = Path("tools") / name
        elif type == "skill":
            base = Path("skills") / f"{name}.yaml"
        else:
            return {"ok": False, "error": f"Unknown resource type: {type}"}

        if base.exists():
            if base.is_dir():
                shutil.rmtree(base)
            else:
                base.unlink()
            return {"ok": True, "action": "deleted", "path": str(base)}
        return {"ok": True, "action": "nothing", "path": str(base)}
    except Exception as e:
        return {"ok": False, "error": str(e)}