"""python_exec -- run a Python script from workspace, not arbitrary code."""
import sys
import subprocess
from pathlib import Path


async def execute(script: str = "", args: str = "", _workspace: str = "", _engine=None) -> dict:
    """Run a Python script file within the workspace.

    Args:
        script: relative path to a .py file within workspace
        args: optional command-line arguments passed to the script
        _workspace: workspace root (injected by engine)
    """
    if not script or not script.strip():
        return {"error": "script must be a non-empty path to a .py file"}

    workspace = Path(_workspace) if _workspace else Path.cwd()
    script_path = (workspace / script).resolve()

    # Security: must be within workspace
    if not str(script_path).startswith(str(workspace.resolve())):
        return {"error": f"script path escapes workspace: {script}"}
    if not script_path.exists():
        return {"error": f"script not found: {script}"}
    if script_path.suffix != '.py':
        return {"error": f"script must be a .py file, got: {script_path.suffix}"}

    cmd = [sys.executable, str(script_path)]
    if args and args.strip():
        cmd.extend(args.strip().split())

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            encoding="utf-8",
            cwd=str(workspace),
            env={**__import__('os').environ, "PYTHONPATH": str(workspace)},
        )
        return {
            "ok": True,
            "stdout": result.stdout[-8000:] if len(result.stdout) > 8000 else result.stdout,
            "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Execution timed out after 30 seconds"}
    except Exception as e:
        return {"error": str(e)}
