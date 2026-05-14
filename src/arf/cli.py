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
    # search subdirectories of cwd for a workspace (e.g. default_workspace/)
    for child in sorted(cwd.iterdir()):
        if child.is_dir() and (child / "arf_agent.yaml").exists():
            return child
    return None


def _require_workspace() -> Path:
    root = _find_workspace_root()
    if root is None:
        print("Error: not in an ARF workspace (no arf_agent.yaml found).")
        print("Run 'arf init' to create one.")
        sys.exit(1)
    return root


def _get_system_resources_dir() -> Path:
    from .resources import manager as _
    import arf.resources.system
    return Path(arf.resources.system.__file__).parent


# ---- init -----------------------------------------------------------


def cmd_init(args):
    from .agent.project import create_workspace, copy_model_config

    name = args.name or "default_workspace"
    parent = Path(args.dir or ".")
    try:
        ws = create_workspace(name, parent)
    except FileExistsError:
        print(f"Error: directory '{name}' already exists")
        sys.exit(1)

    cwd_root = _find_workspace_root()
    if cwd_root and cwd_root != ws:
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


# ---- port utilities --------------------------------------------------

def _check_port(host: str, port: int) -> bool:
    """Return True if *port* on *host* is already in use."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _get_port_pid(port: int) -> int | None:
    """Return PID of the process listening on *port*, or None."""
    import subprocess as _sp
    import re
    result = _sp.run(
        ["ss", "-tlnp", f"sport = :{port}"],
        capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        m = re.search(r'pid=(\d+)', line)
        if m:
            return int(m.group(1))
    return None


def _resolve_port(args, host: str) -> int:
    """Resolve port: honour -f, or print warning and pick the next free port."""
    import time as _time
    port = args.port
    if not _check_port(host, port):
        return port

    if getattr(args, "force", False):
        pid = _get_port_pid(port)
        if pid is not None:
            print(f"Port {port} is in use (pid={pid}), force-killing...")
            _kill_process(pid, f"port-{port}-holder")
            _time.sleep(0.5)
            if not _check_port(host, port):
                return port
        print(f"Error: could not free port {port}")
        sys.exit(1)

    # No -f: try next available port
    for offset in range(1, 101):
        candidate = port + offset
        if not _check_port(host, candidate):
            print(f"Port {port} is in use, falling back to {candidate}")
            return candidate
    print(f"Error: port {port} is occupied and no free port found in range {port}-{port + 100}")
    sys.exit(1)


# ---- start ----------------------------------------------------------


def cmd_start(args):
    """Start both backend server and frontend dev server."""
    import subprocess
    import time
    import json

    ws_dir = args.workspace or str(_require_workspace() if args.workspace is None else Path(args.workspace))
    ws = Path(ws_dir)
    run_dir = _run_dir(ws)

    # Resolve port before anything else
    port = _resolve_port(args, args.host)

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
        "port": port,
    }
    (run_dir / "run.json").write_text(json.dumps(run_cfg))

    fe_proc = None
    fe_env = None
    try:
        if frontend_dir:
            print(f"Starting frontend dev server (Vite)...")
            fe_env = {**os.environ, "VITE_BACKEND_PORT": str(port)}
            fe_proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(frontend_dir),
                env=fe_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            _write_pid(run_dir, "frontend", fe_proc.pid)
            time.sleep(1.5)

        from .server import ARFServer

        _write_pid(run_dir, "backend", os.getpid())
        print(f"Starting ARF server at http://{args.host}:{port}")
        print(f"  Workspace:  {ws_dir}")
        if frontend_dir:
            print(f"  Frontend:   http://localhost:5173")
        print(f"  Press Ctrl+C to stop all services.")
        server = ARFServer(workspace_dir=ws_dir)
        server.start(host=args.host, port=port)
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
                args.host = cfg.get("host", "localhost")
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


# ---- main -----------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="arf",
        description="ARF -- Agent Resource Framework",
    )
    subparsers = parser.add_subparsers(dest="command")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize a new ARF workspace")
    init_parser.add_argument("name", nargs="?", default="default_workspace",
                             help="Workspace name (default: default_workspace)")
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
    start_parser.add_argument("-f", "--force", action="store_true",
                              help="Force-kill any process occupying the target port")

    # stop
    stop_parser = subparsers.add_parser("stop", help="Stop a running ARF session")
    stop_parser.add_argument("--workspace", "-w", default=None, help="Workspace directory")

    # reload
    reload_parser = subparsers.add_parser("reload", help="Restart a running ARF session")
    reload_parser.add_argument("--workspace", "-w", default=None, help="Workspace directory")
    reload_parser.add_argument("--host", default="localhost", help="Backend listen address")
    reload_parser.add_argument("--port", type=int, default=8000, help="Backend listen port")
    reload_parser.add_argument("-f", "--force", action="store_true",
                               help="Force-kill any process occupying the target port")

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

    commands[args.command](args)


if __name__ == "__main__":
    main()
