"""python_exec -- execute Python code in a subprocess."""
import sys
import subprocess


async def execute(code: str) -> dict:
    if not code or not isinstance(code, str):
        return {"error": "code must be a non-empty string"}
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=30,
            text=True,
        )
        return {
            "ok": True,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Execution timed out after 30 seconds"}
    except Exception as e:
        return {"error": str(e)}
