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
    print(f"Next: cd {name} && arf start")


# ---- web ------------------------------------------------------------


def cmd_web(args):
    from .server import ARFServer

    ws_dir = args.workspace or str(_require_workspace() if args.workspace is None else Path(args.workspace))
    server = ARFServer(workspace_dir=ws_dir)
    print(f"Starting ARF server at http://{args.host}:{args.port}")
    print(f"  Workspace: {ws_dir}")
    server.start(host=args.host, port=args.port)


# ---- serve (multi-user) ---------------------------------------------


# ---- start ----------------------------------------------------------


# ---- run state -------------------------------------------------------

def _run_dir(ws: Path) -> Path:
    d = ws / ".arf"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_pid(run_dir: Path, name: str, pid: int):
    (run_dir / f"{name}.pid").write_text(str(pid))


def _read_pid(run_dir: Path, name: str) -> int | None:
    f = run_dir / f"{name}.pid"
    if f.exists():
        try:
            return int(f.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def _clear_run_state(run_dir: Path):
    for f in run_dir.glob("*.pid"):
        f.unlink(missing_ok=True)
    cfg = run_dir / "run.json"
    cfg.unlink(missing_ok=True)


def _kill_process(pid: int, name: str, grace: int = 3):
    """Kill a process by PID, with fallback to process group."""
    import subprocess
    try:
        os.kill(pid, 0)  # check if exists
    except OSError:
        return False
    print(f"Stopping {name} (pid={pid})...")
    try:
        os.kill(pid, 15)  # SIGTERM
        import time
        for _ in range(grace):
            time.sleep(1)
            try:
                os.kill(pid, 0)
            except OSError:
                return True
        os.kill(pid, 9)  # SIGKILL
        return True
    except OSError:
        return True


# ---- start ----------------------------------------------------------


def cmd_start(args):
    """Start both backend server and frontend dev server."""
    import subprocess
    import time
    import json

    ws_dir = args.workspace or str(_require_workspace() if args.workspace is None else Path(args.workspace))
    ws = Path(ws_dir)
    run_dir = _run_dir(ws)

    # Check if already running
    be_pid = _read_pid(run_dir, "backend")
    if be_pid is not None:
        try:
            os.kill(be_pid, 0)
            print(f"Error: ARF is already running (pid={be_pid}). Use 'arf stop' first.")
            sys.exit(1)
        except OSError:
            _clear_run_state(run_dir)

    # Locate frontend directory relative to the package
    package_dir = Path(__file__).parent  # src/arf/
    frontend_dir = package_dir.parent.parent / "frontend"

    if not (frontend_dir / "package.json").exists():
        print(f"Warning: frontend not found at {frontend_dir}")
        print("Starting backend only...")
        frontend_dir = None

    # Save run config for reload
    run_cfg = {
        "workspace": str(ws),
        "host": args.host,
        "port": args.port,
    }
    (run_dir / "run.json").write_text(json.dumps(run_cfg))

    fe_proc = None
    try:
        if frontend_dir:
            print(f"Starting frontend dev server (Vite)...")
            fe_proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(frontend_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            _write_pid(run_dir, "frontend", fe_proc.pid)
            time.sleep(1.5)

        from .server import ARFServer

        _write_pid(run_dir, "backend", os.getpid())
        print(f"Starting ARF server at http://{args.host}:{args.port}")
        print(f"  Workspace:  {ws_dir}")
        if frontend_dir:
            print(f"  Frontend:   http://localhost:5173")
        print(f"  Press Ctrl+C to stop all services.")
        server = ARFServer(workspace_dir=ws_dir)
        server.start(host=args.host, port=args.port)
    except KeyboardInterrupt:
        pass
    finally:
        print("Shutting down...")
        if fe_proc and fe_proc.poll() is None:
            fe_proc.terminate()
            try:
                fe_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                fe_proc.kill()
            print("Frontend stopped.")
        _clear_run_state(run_dir)


# ---- stop -----------------------------------------------------------


def cmd_stop(args):
    """Stop a running ARF session."""
    import json

    ws_dir = args.workspace or str(_require_workspace() if args.workspace is None else Path(args.workspace))
    ws = Path(ws_dir)
    run_dir = _run_dir(ws)

    stopped = False

    be_pid = _read_pid(run_dir, "backend")
    if be_pid is not None:
        stopped |= _kill_process(be_pid, "Backend")
        # Also kill child processes (uvicorn workers)
        import subprocess
        subprocess.run(["pkill", "-P", str(be_pid)], capture_output=True, timeout=3)

    fe_pid = _read_pid(run_dir, "frontend")
    if fe_pid is not None:
        stopped |= _kill_process(fe_pid, "Frontend")

    _clear_run_state(run_dir)

    if not stopped:
        # Fallback: search by workspace path
        import subprocess
        result = subprocess.run(
            ["pgrep", "-f", f"arf.*{ws.name}"],
            capture_output=True, text=True,
        )
        if result.stdout.strip():
            for pid_str in result.stdout.strip().split("\n"):
                if pid_str:
                    _kill_process(int(pid_str), f"ARF (pid={pid_str})")
            stopped = True

    if not stopped:
        print("No running ARF instance found.")
    else:
        print("ARF stopped.")


# ---- reload ---------------------------------------------------------


def cmd_reload(args):
    """Restart a running ARF session."""
    import json

    ws_dir = args.workspace or str(_require_workspace() if args.workspace is None else Path(args.workspace))
    ws = Path(ws_dir)
    run_dir = _run_dir(ws)
    run_cfg_file = run_dir / "run.json"

    # Load previous run config if available
    if run_cfg_file.exists():
        try:
            cfg = json.loads(run_cfg_file.read_text())
            args.workspace = cfg.get("workspace", ws_dir)
            if not args.host:
                args.host = cfg.get("host", "0.0.0.0")
            if not args.port:
                args.port = cfg.get("port", 8000)
        except (json.JSONDecodeError, OSError):
            pass

    print("Reloading ARF...")
    cmd_stop(args)
    import time
    time.sleep(1)
    cmd_start(args)


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

    # start
    start_parser = subparsers.add_parser("start", help="Start backend + frontend in one command")
    start_parser.add_argument("--workspace", "-w", default=None, help="Workspace directory")
    start_parser.add_argument("--host", default="0.0.0.0", help="Backend listen address")
    start_parser.add_argument("--port", type=int, default=8000, help="Backend listen port")

    # stop
    stop_parser = subparsers.add_parser("stop", help="Stop a running ARF session")
    stop_parser.add_argument("--workspace", "-w", default=None, help="Workspace directory")

    # reload
    reload_parser = subparsers.add_parser("reload", help="Restart a running ARF session")
    reload_parser.add_argument("--workspace", "-w", default=None, help="Workspace directory")

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
        "start": cmd_start,
        "stop": cmd_stop,
        "reload": cmd_reload,
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
