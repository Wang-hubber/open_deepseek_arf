"""Git pusher -- push workspace tools/ and skills/ to remote repository."""

import subprocess
from pathlib import Path


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from start to find the nearest .git directory."""
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out after 30s"
    except FileNotFoundError:
        return -1, "", "git executable not found"


def execute(workspace_dir: str, message: str) -> dict:
    """Stage tools/ and skills/ dirs, commit with message, push to origin."""
    ws = Path(workspace_dir).resolve()
    if not ws.exists():
        return {"error": f"Workspace directory not found: {ws}"}

    repo = _find_repo_root(ws)
    if repo is None:
        return {"error": f"No .git repository found above {ws}"}

    # Compute relative paths from repo root to workspace subdirs
    try:
        rel_ws = ws.relative_to(repo)
    except ValueError:
        return {"error": f"Workspace {ws} is not inside repo {repo}"}

    tools_dir = str(rel_ws / "tools")
    skills_dir = str(rel_ws / "skills")

    # 1. Stage only tools/ and skills/
    rc, stdout, stderr = _run(["git", "add", tools_dir, skills_dir], repo)
    if rc != 0:
        return {"error": f"git add failed: {stderr or stdout}"}

    # 2. Check if there's anything staged
    rc, stdout, stderr = _run(
        ["git", "diff", "--cached", "--name-only"], repo,
    )
    if rc != 0:
        return {"error": f"git diff failed: {stderr or stdout}"}

    staged_files = stdout
    if not staged_files:
        return {
            "ok": True,
            "pushed": False,
            "message": "No changes to push in tools/ or skills/.",
        }

    # 3. Commit
    rc, stdout, stderr = _run(
        ["git", "commit", "-m", message], repo,
    )
    if rc != 0:
        return {"error": f"git commit failed: {stderr or stdout}"}
    commit_hash = stdout.strip().split()[-1] if stdout else "unknown"

    # 4. Push
    rc, stdout, stderr = _run(
        ["git", "push", "origin", "HEAD"], repo,
    )
    if rc != 0:
        return {"error": f"git push failed: {stderr or stdout}"}

    return {
        "ok": True,
        "pushed": True,
        "commit": commit_hash,
        "remote": "origin",
        "files": [f.strip() for f in staged_files.split("\n") if f.strip()],
    }
