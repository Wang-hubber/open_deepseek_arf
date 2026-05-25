#!/usr/bin/env python3
"""ARF Default Assistant CLI -- manage the assistant server and resources."""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from agent_main import app_context
APP_DIR = app_context.root


def _httpx_get(path: str) -> dict | None:
    try:
        import httpx
        resp = httpx.get(f"http://127.0.0.1:8000{path}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def _httpx_post(path: str, data: dict | None = None) -> dict | None:
    try:
        import httpx
        resp = httpx.post(f"http://127.0.0.1:8000{path}", json=data, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def cmd_init(args):
    """Create skeleton directories for the assistant."""
    dirs = ["tools", "skills", "hooks", "memory", "workspaces/default"]
    for d in dirs:
        (APP_DIR / d).mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")
    print("Initialization complete.")


def cmd_chat(args):
    """Send a message to the assistant."""
    result = _httpx_post("/api/chat", {"message": args.message})
    if result:
        print(result.get("content", ""))


def cmd_start(args):
    """Launch uvicorn (and optionally npm run dev)."""
    # Kill any existing process on port
    _kill_port(8000)

    # Build frontend
    frontend_dir = APP_DIR / ".." / "web"
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    has_frontend = frontend_dir.exists() and (frontend_dir / "package.json").exists()
    if has_frontend:
        if not (frontend_dir / "node_modules").exists():
            print("Installing frontend dependencies...")
            subprocess.run([npm_cmd, "install"], cwd=str(frontend_dir), check=True)
        print("Building frontend...")
        subprocess.run([npm_cmd, "run", "build"], cwd=str(frontend_dir), check=True)

    # Setup log directory
    log_dir = APP_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    server_log = open(log_dir / "server.log", "a", encoding="utf-8")

    # Start uvicorn — write to log file
    uvicorn_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(APP_DIR),
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    print(f"Server starting (PID {uvicorn_proc.pid}), logs: logs{os.sep}server.log")
    time.sleep(2)

    print(f"")
    print(f"  →  http://127.0.0.1:8000")
    print(f"  Logs: {log_dir}")
    print(f"")
    print(f"Press Ctrl+C to stop.")
    try:
        uvicorn_proc.wait()
    except KeyboardInterrupt:
        uvicorn_proc.terminate()


def _kill_port(port: int):
    """Kill process on the given port."""
    try:
        import subprocess
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass
            print(f"Killed process(es) on port {port}")
    except Exception:
        pass


def cmd_stop(args):
    """Stop the server."""
    _kill_port(8000)
    print("Server stopped.")


def cmd_reload(args):
    """Reload the server (stop then start)."""
    cmd_stop(args)
    time.sleep(1)
    cmd_start(args)


def cmd_web(args):
    """Launch uvicorn only (no frontend)."""
    _kill_port(8000)
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(APP_DIR),
    )


def cmd_run(args):
    """Find and print a skill definition."""
    result = _httpx_get("/api/resources/skills")
    if result:
        for skill in result.get("items", []):
            if skill.get("name") == args.skill:
                print(f"Name: {skill['name']}")
                print(f"Description: {skill['description']}")
                print(f"Tools: {', '.join(skill.get('tools', []))}")
                return
        print(f"Skill '{args.skill}' not found.")


def cmd_list(args):
    """List resources (tools, skills, models)."""
    type_map = {"tools": "tools", "skills": "skills", "models": "models"}
    t = type_map.get(args.type, "tools")
    result = _httpx_get(f"/api/resources/{t}")
    if result:
        print(f"{result['type'].capitalize()} ({result['count']}):")
        for item in result.get("items", []):
            print(f"  - {item['name']}: {item.get('description', '')}")


def cmd_validate(args):
    """Validate agent.yaml and tool directories exist."""
    agent_yaml = APP_DIR / "agent.yaml"
    if not agent_yaml.exists():
        print("ERROR: agent.yaml not found")
        return False

    print(f"OK   agent.yaml ({agent_yaml.stat().st_size} bytes)")

    import yaml
    cfg = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
    tools = cfg.get("tools", [])
    for t in tools:
        name = t.get("name", "")
        tool_dir = APP_DIR / "tools" / name
        if tool_dir.exists():
            print(f"OK   tools/{name}/")
        else:
            print(f"MISS tools/{name}/")

    skills_dir = APP_DIR / "skills"
    for skill_cfg in cfg.get("skills", []):
        name = skill_cfg.get("name", "")
        skill_path = skills_dir / f"{name}.yaml"
        if skill_path.exists():
            print(f"OK   skills/{name}.yaml")
        else:
            print(f"MISS skills/{name}.yaml")

    hooks_dir = APP_DIR / "hooks"
    for hook in cfg.get("hooks", []):
        hook_name = hook.get("name", "")
        hook_run = hook.get("run", [])
        for run_cmd in hook_run:
            parts = run_cmd.split()
            if parts and parts[0].startswith("python"):
                hook_path = hooks_dir / parts[1]
                if hook_path.exists():
                    print(f"OK   hooks/{parts[1]}")
                else:
                    print(f"MISS hooks/{parts[1]}")

    print("Validation complete.")
    return True


def cmd_config_generate(args):
    """Scan filesystem resources and dump agent.yaml to stdout."""
    import asyncio
    import yaml
    from arf.resources.providers.tool_provider import ToolProvider
    from arf.resources.providers.skill_provider import SkillProvider
    from arf.resources.providers.model_provider import ModelProvider
    from arf.resources.resolver import ResourceResolver

    async def _run():
        tp = ToolProvider(APP_DIR / "tools")
        sp = SkillProvider(APP_DIR / "skills")
        mp = ModelProvider(APP_DIR / "models")
        resolver = ResourceResolver(tp, sp, mp)
        config = await resolver.generate_config()
        config["name"] = "arf_assistant"
        config["description"] = "Auto-generated config — edit to add overrides"
        return config

    config = asyncio.run(_run())
    print(yaml.dump(config, allow_unicode=True, default_flow_style=False))


def cmd_clone(args):
    """Clone a system tool/skill to the workspace."""
    src_root = APP_DIR / ".." / ".." / "src" / "arf" / "resources" / "system"
    type_plural = args.type + "s"

    if args.type == "tool":
        src = src_root / type_plural / args.name
        dst = APP_DIR / "tools" / args.name
    elif args.type == "skill":
        src = src_root / type_plural / f"{args.name}.yaml"
        dst = APP_DIR / "skills" / f"{args.name}.yaml"
    else:
        print(f"Unknown type: {args.type}")
        return

    if not src.exists():
        print(f"Source not found: {src}")
        return

    if dst.exists():
        print(f"Destination already exists: {dst}")
        return

    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        import shutil
        shutil.copytree(src, dst)
        print(f"Cloned {args.type} '{args.name}' to {dst}")
    else:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Cloned {args.type} '{args.name}' to {dst}")


def main():
    parser = argparse.ArgumentParser(description="ARF Default Assistant CLI")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    p_init = sub.add_parser("init", help="Create skeleton directories")
    p_init.set_defaults(func=cmd_init)

    p_chat = sub.add_parser("chat", help="Send a message to the assistant")
    p_chat.add_argument("message", help="Message to send")
    p_chat.set_defaults(func=cmd_chat)

    p_start = sub.add_parser("start", help="Launch server (and frontend)")
    p_start.add_argument("--no-frontend", action="store_true", help="Skip frontend")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop server")
    p_stop.set_defaults(func=cmd_stop)

    p_reload = sub.add_parser("reload", help="Reload server")
    p_reload.set_defaults(func=cmd_reload)

    p_web = sub.add_parser("web", help="Launch uvicorn only")
    p_web.set_defaults(func=cmd_web)

    p_run = sub.add_parser("run", help="Show a skill definition")
    p_run.add_argument("skill", help="Skill name")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="List resources")
    p_list.add_argument("type", nargs="?", default="tools", choices=["tools", "skills", "models"])
    p_list.set_defaults(func=cmd_list)

    p_validate = sub.add_parser("validate", help="Validate workspace resources")
    p_validate.set_defaults(func=cmd_validate)

    p_clone = sub.add_parser("clone", help="Clone a system resource")
    p_clone.add_argument("type", choices=["tool", "skill"])
    p_clone.add_argument("name", help="Resource name")
    p_clone.set_defaults(func=cmd_clone)

    # Config management
    p_config = sub.add_parser("config", help="Config management")
    p_config_sub = p_config.add_subparsers(dest="config_cmd")
    p_gen = p_config_sub.add_parser("generate", help="Generate agent.yaml from filesystem")
    p_gen.set_defaults(func=cmd_config_generate)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    if args.command == "config" and getattr(args, "config_cmd", None) is None:
        p_config.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
