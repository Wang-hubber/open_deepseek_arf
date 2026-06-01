"""bash — cross-platform shell command execution within workspace."""
import os
import platform
import subprocess
from pathlib import Path


async def execute(command: str = "", _workspace: str = "") -> dict:
    """Run a shell command in the workspace directory.

    On Linux/Mac uses /bin/sh. On Windows uses cmd.exe.
    """
    if not command or not command.strip():
        return {"error": "command must be a non-empty string"}

    workspace = Path(_workspace) if _workspace else Path.cwd()
    cwd = str(workspace.resolve())

    # Choose shell per platform
    system = platform.system()
    if system == "Windows":
        shell = os.environ.get("COMSPEC", "cmd.exe")
        shell_flag = "/c"
    else:
        shell = os.environ.get("SHELL", "/bin/sh")
        shell_flag = "-c"

    try:
        result = subprocess.run(
            [shell, shell_flag, command],
            capture_output=True,
            timeout=30,
            encoding="utf-8",
            cwd=cwd,
            env={**os.environ, "PWD": cwd},
        )
        stdout = result.stdout[-8000:] if len(result.stdout) > 8000 else result.stdout
        stderr = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr
        return {
            "ok": True,
            "stdout": stdout or "(no output)",
            "stderr": stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after 30s: {command[:100]}"}
    except Exception as e:
        return {"error": str(e)}
