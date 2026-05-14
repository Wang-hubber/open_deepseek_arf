"""CLI entry point for ARF framework."""

import argparse
import os
import shutil
import sys
from pathlib import Path


def _find_workspace_root() -> Path | None:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "arf_agent.yaml").exists():
            return parent
    return None


def _require_workspace() -> Path:
    root = _find_workspace_root()
    if root is None:
        print("Error: not in an ARF workspace (no arf_agent.yaml found).")
        print("Run 'arf init <name>' to create one.")
        sys.exit(1)
    return root


def _get_system_resources_dir() -> Path:
    from .resources import manager as _
    import arf.resources.system
    return Path(arf.resources.system.__file__).parent


# ---- init -----------------------------------------------------------


def cmd_init(args):
    from .agent.project import create_workspace, copy_model_config

    name = args.name
    parent = Path(args.dir or ".")
    try:
        ws = create_workspace(name, parent)
    except FileExistsError:
        print(f"Error: directory '{name}' already exists")
        sys.exit(1)

    cwd_root = _find_workspace_root()
    if cwd_root:
        src_cfg = cwd_root / "models" / "deep_thinking" / "config.yaml"
        copy_model_config(src_cfg, ws)

    print(f"Workspace '{name}' created at {ws}")
    print(f"  arf_agent.yaml  -- workspace configuration")
    print(f"  models/         -- user model configs")
    print(f"  tools/          -- user tools")
    print(f"  skills/         -- user skills")
    print(f"  memory/         -- session memory")
    print(f"")
    print(f"Next: cd {name} && arf web")


# ---- web ------------------------------------------------------------


def cmd_web(args):
    from .server import ARFServer

    ws_dir = args.workspace or str(_require_workspace() if args.workspace is None else Path(args.workspace))
    server = ARFServer(workspace_dir=ws_dir)
    print(f"Starting ARF server at http://{args.host}:{args.port}")
    print(f"  Workspace: {ws_dir}")
    server.start(host=args.host, port=args.port)


# ---- serve (multi-user) ---------------------------------------------


# ---- chat -----------------------------------------------------------


def cmd_chat(args):
    print("arf chat: not yet implemented")


# ---- run ------------------------------------------------------------


def cmd_run(args):
    print("arf run: not yet implemented")


# ---- list -----------------------------------------------------------


def cmd_list(args):
    from .resources.manager import ResourceRegistry

    registry = ResourceRegistry()
    system_dir = str(_get_system_resources_dir())
    ws = _find_workspace_root()
    registry.load(system_dir, str(ws) if ws else None)

    if args.type == "tools":
        _print_resource_list("Tools", registry._items["tools"])
    elif args.type == "skills":
        _print_resource_list("Skills", registry._items["skills"])
    elif args.type == "models":
        _print_resource_list("Models", registry._items["models"])
    else:
        for rtype, label in [("models", "Models"), ("tools", "Tools"), ("skills", "Skills")]:
            _print_resource_list(label, registry._items[rtype])
            print()


def _print_resource_list(label: str, items: dict):
    print(f"{label} ({len(items)}):")
    if not items:
        print("  (none)")
        return
    for name, r in sorted(items.items()):
        source_tag = "[sys]" if r.get("source") == "system" else "[usr]"
        desc = r.get("description", "")
        line = f"  {source_tag} {name}"
        if desc:
            line += f" -- {desc}"
        print(line)


# ---- validate -------------------------------------------------------


def cmd_validate(args):
    ws = _require_workspace()
    errors = []

    for rtype in ("tools", "skills"):
        rdir = ws / rtype
        if not rdir.exists():
            continue
        for sub in sorted(rdir.iterdir()):
            if not sub.is_dir():
                continue
            _validate_resource(ws, rtype, sub, errors)

    if errors:
        print(f"Validation failed with {len(errors)} issue(s):")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("Validation passed -- all resources are valid.")


def _validate_resource(ws: Path, rtype: str, sub: Path, errors: list):
    import yaml as _yaml

    yaml_file = sub / f"{rtype[:-1]}.yaml"
    name = sub.name

    if not yaml_file.exists():
        errors.append(f"[{rtype}/{name}] Missing {rtype[:-1]}.yaml")
        return

    try:
        with open(yaml_file) as f:
            cfg = _yaml.safe_load(f)
    except Exception as e:
        errors.append(f"[{rtype}/{name}] Invalid YAML: {e}")
        return

    if cfg is None:
        errors.append(f"[{rtype}/{name}] Empty or invalid {rtype[:-1]}.yaml")
        return

    if not cfg.get("name"):
        errors.append(f"[{rtype}/{name}] Missing 'name' field")

    if rtype == "tools":
        func_file = sub / "function.py"
        if not func_file.exists():
            errors.append(f"[tools/{name}] Missing function.py")
        else:
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(f"validate_{name}", func_file)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if not callable(getattr(mod, "execute", None)):
                    errors.append(f"[tools/{name}] function.py has no execute() function")
            except Exception as e:
                errors.append(f"[tools/{name}] Failed to load function.py: {e}")

    if rtype == "skills":
        for tool_name in cfg.get("tools", []):
            tool_dir = ws / "tools" / tool_name
            sys_tool_dir = _get_system_resources_dir() / "tools" / tool_name
            if not tool_dir.exists() and not sys_tool_dir.exists():
                errors.append(f"[skills/{name}] References missing tool: {tool_name}")


# ---- clone ----------------------------------------------------------


def cmd_clone(args):
    ws = _require_workspace()
    resource_name = args.resource_name
    rtype = args.type

    sys_dir = _get_system_resources_dir()
    src = sys_dir / rtype / resource_name

    if not src.exists():
        print(f"Error: system {rtype[:-1]} '{resource_name}' not found")
        sys.exit(1)

    dst = ws / rtype / resource_name
    if dst.exists():
        print(f"Error: {dst} already exists")
        sys.exit(1)

    shutil.copytree(src, dst)
    print(f"Cloned [{rtype[:-1]}] {resource_name} -> {dst}")
    print("You can now edit it freely.")


# ---- vault ---------------------------------------------------------


def cmd_vault_init(args):
    from getpass import getpass
    from .security import init_vault, status

    ws = _require_workspace()
    if status(str(ws))["initialized"]:
        print("Vault already exists in this workspace.")
        sys.exit(1)

    password = getpass("Enter master password: ")
    if len(password) < 4:
        print("Error: password must be at least 4 characters")
        sys.exit(1)
    confirm = getpass("Confirm master password: ")
    if password != confirm:
        print("Error: passwords do not match")
        sys.exit(1)

    key, data = init_vault(str(ws), password)
    print(f"Vault created in {ws}")

    cred_path = ws / "credentials.yaml"
    if cred_path.exists():
        import yaml as _yaml
        from .security import save_encrypted
        try:
            cred = _yaml.safe_load(cred_path.read_text()) or {}
            if cred.get("email") or cred.get("auth_code"):
                data["credentials"] = {
                    "email": cred.get("email", ""),
                    "auth_code": cred.get("auth_code", ""),
                }
                save_encrypted(ws, key, data)
                print("  Migrated credentials.yaml into vault")
        except Exception as e:
            print(f"  Warning: failed to migrate credentials: {e}")

    print("  Vault key will be cleared when this process exits")


def cmd_vault_unlock(args):
    from getpass import getpass
    from .security import unlock_vault, status

    ws = _require_workspace()
    st = status(str(ws))
    if not st["initialized"]:
        print("No vault found in this workspace. Run 'arf vault init' first.")
        sys.exit(1)

    password = getpass("Enter master password: ")
    try:
        key, data = unlock_vault(str(ws), password)
        print("Vault unlocked.")
        if data.get("credentials", {}).get("email"):
            print(f"  Email: {data['credentials']['email']}")
    except ValueError:
        print("Error: incorrect password")
        sys.exit(1)


def cmd_vault_lock(args):
    print("Vault lock is managed per-session. The key is held in process memory.")
    print("Use the web UI or restart the CLI to clear it.")


def cmd_vault_status(args):
    from .security import status

    ws = _require_workspace()
    st = status(str(ws))
    print(f"Vault initialized: {st['initialized']}")
    print(f"Vault state is managed per-session in the web server.")


# ---- main -----------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="arf",
        description="ARF -- Agent Resource Framework",
    )
    subparsers = parser.add_subparsers(dest="command")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize a new ARF workspace")
    init_parser.add_argument("name", help="Workspace name (snake_case)")
    init_parser.add_argument("--dir", "-d", default=".", help="Parent directory")

    # web
    web_parser = subparsers.add_parser("web", help="Start the ARF web server")
    web_parser.add_argument("--workspace", "-w", default=None, help="Workspace directory")
    web_parser.add_argument("--host", default="0.0.0.0", help="Listen address")
    web_parser.add_argument("--port", type=int, default=8000, help="Listen port")

    # chat
    subparsers.add_parser("chat", help="Start a terminal chat session")

    # run
    run_parser = subparsers.add_parser("run", help="Run a configured skill")
    run_parser.add_argument("skill_name", help="Skill name to run")

    # list
    list_parser = subparsers.add_parser("list", help="List registered resources")
    list_parser.add_argument("type", nargs="?", default="all",
                             choices=["tools", "skills", "models", "all"],
                             help="Resource type to list (default: all)")

    # validate
    subparsers.add_parser("validate", help="Validate workspace resource integrity")

    # clone
    clone_parser = subparsers.add_parser("clone", help="Clone a system resource to user workspace")
    clone_parser.add_argument("type", choices=["tools", "skills", "models"],
                              help="Resource type to clone")
    clone_parser.add_argument("resource_name", help="Name of the system resource to clone")

    # vault
    vault_parser = subparsers.add_parser("vault", help="Manage the encrypted vault")
    vault_sub = vault_parser.add_subparsers(dest="vault_command")
    vault_sub.add_parser("init", help="Initialize a new vault")
    vault_sub.add_parser("unlock", help="Unlock the vault")
    vault_sub.add_parser("lock", help="Lock the vault")
    vault_sub.add_parser("status", help="Show vault status")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "web": cmd_web,
        "chat": cmd_chat,
        "run": cmd_run,
        "list": cmd_list,
        "validate": cmd_validate,
        "clone": cmd_clone,
    }

    if args.command == "vault":
        vault_cmds = {
            "init": cmd_vault_init,
            "unlock": cmd_vault_unlock,
            "lock": cmd_vault_lock,
            "status": cmd_vault_status,
        }
        handler = vault_cmds.get(args.vault_command)
        if handler is None:
            vault_parser.print_help()
            sys.exit(1)
        handler(args)
    else:
        commands[args.command](args)


if __name__ == "__main__":
    main()
