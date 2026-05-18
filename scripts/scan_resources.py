"""Scan all system resources and produce a CSV inventory.

Columns: type, name, has_yaml, has_function_py, has_config_default,
         description, source, depends_on, required, configured
"""
import csv
import sys
from pathlib import Path
from typing import List, Dict

SRC = Path(__file__).parent.parent / "src" / "arf" / "resources" / "system"

HEADER = [
    "type", "name", "has_yaml", "has_function_py", "has_config_default",
    "description", "source", "depends_on", "required", "configured",
    "notes",
]

def scan_tools(tools_dir: Path) -> List[Dict]:
    rows = []
    if not tools_dir.exists():
        return rows
    for sub in sorted(tools_dir.iterdir()):
        if not sub.is_dir():
            continue
        name = sub.name
        has_yaml = (sub / "tool.yaml").exists()
        has_func = (sub / "function.py").exists()
        has_cfg = (sub / "config_default.yaml").exists()
        desc = ""
        depends_on = ""
        required = ""
        if has_yaml:
            import yaml
            with open(sub / "tool.yaml") as f:
                data = yaml.safe_load(f) or {}
            desc = data.get("description", "")
        if has_cfg:
            import yaml
            with open(sub / "config_default.yaml") as f:
                data = yaml.safe_load(f) or {}
            depends_on = str(data.get("depends_on", []))
            required = str(data.get("required", False))
        notes = ""
        if not has_func:
            notes = "CONFIG_STUB: no function.py"
        rows.append({
            "type": "tool", "name": name,
            "has_yaml": str(has_yaml), "has_function_py": str(has_func),
            "has_config_default": str(has_cfg), "description": desc,
            "source": "system", "depends_on": depends_on,
            "required": required, "configured": "True",
            "notes": notes,
        })
    return rows

def scan_skills(skills_dir: Path) -> List[Dict]:
    rows = []
    if not skills_dir.exists():
        return rows
    for sub in sorted(skills_dir.iterdir()):
        if not sub.is_dir():
            continue
        name = sub.name
        has_yaml = (sub / "skill.yaml").exists()
        has_cfg = (sub / "config_default.yaml").exists()
        desc = ""
        depends_on = ""
        required = ""
        tools_ref = ""
        if has_yaml:
            import yaml
            with open(sub / "skill.yaml") as f:
                data = yaml.safe_load(f) or {}
            desc = data.get("description", "")
            tools_ref = str(data.get("tools", []))
        if has_cfg:
            import yaml
            with open(sub / "config_default.yaml") as f:
                data = yaml.safe_load(f) or {}
            depends_on = str(data.get("depends_on", []))
            required = str(data.get("required", False))
        notes = ""
        if not has_yaml:
            notes = "CONFIG_STUB: no skill.yaml"
        rows.append({
            "type": "skill", "name": name,
            "has_yaml": str(has_yaml), "has_function_py": "N/A",
            "has_config_default": str(has_cfg), "description": desc,
            "source": "system", "depends_on": depends_on,
            "required": required, "configured": "True",
            "notes": notes,
        })
    return rows

def scan_models(models_dir: Path) -> List[Dict]:
    rows = []
    if not models_dir.exists():
        return rows
    for sub in sorted(models_dir.iterdir()):
        if not sub.is_dir():
            continue
        name = sub.name
        has_cfg = (sub / "config_default.yaml").exists()
        desc = ""
        model_type = ""
        depends_on = ""
        required = ""
        context_window = ""
        if has_cfg:
            import yaml
            with open(sub / "config_default.yaml") as f:
                data = yaml.safe_load(f) or {}
            desc = data.get("description", "")
            model_type = data.get("model_type", "")
            depends_on = str(data.get("depends_on", []))
            required = str(data.get("required", False))
            context_window = str(data.get("context_window", ""))
        rows.append({
            "type": "model", "name": name,
            "has_yaml": "N/A", "has_function_py": "N/A",
            "has_config_default": str(has_cfg), "description": desc,
            "source": "system", "depends_on": depends_on,
            "required": required, "configured": "False",
            "notes": f"model_type={model_type} context_window={context_window}",
        })
    return rows

def main():
    all_rows = []
    all_rows.extend(scan_tools(SRC / "tools"))
    all_rows.extend(scan_skills(SRC / "skills"))
    all_rows.extend(scan_models(SRC / "models"))

    out = Path("docs/superpowers/assessment/resource_inventory.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    # Summary
    tools = [r for r in all_rows if r["type"] == "tool"]
    skills = [r for r in all_rows if r["type"] == "skill"]
    models = [r for r in all_rows if r["type"] == "model"]

    tool_stubs = [t for t in tools if "CONFIG_STUB" in t["notes"]]
    skill_stubs = [s for s in skills if "CONFIG_STUB" in s["notes"]]

    print(f"Tools: {len(tools)} total, {len(tool_stubs)} config-only stubs ({', '.join(t['name'] for t in tool_stubs)})")
    print(f"Skills: {len(skills)} total, {len(skill_stubs)} config-only stubs ({', '.join(s['name'] for s in skill_stubs)})")
    print(f"Models: {len(models)} total")
    print(f"")
    print(f"Inventory written to {out}")

if __name__ == "__main__":
    main()
