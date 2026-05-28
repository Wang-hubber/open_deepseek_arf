"""User-triggered memory extraction via tool call."""
import json
import subprocess
from pathlib import Path


async def execute(session_id: str = "", memory_dir: str = "",
                  force: bool = False) -> dict:
    """Read session state from FileStateStore, spawn extractor subprocess."""
    state_dir = Path(memory_dir or "./memory") / "state"
    state_file = state_dir / f"{session_id}.json"

    if not state_file.exists():
        return {"ok": False, "error": f"No session state found: {state_file}"}

    state = json.loads(state_file.read_text())
    messages = state.get("messages", [])
    if not messages:
        return {"ok": False, "error": "No messages to extract from"}

    tmp_file = state_dir / f"extract_{session_id}.json"
    tmp_file.write_text(json.dumps(messages, ensure_ascii=False))

    extractor = Path(__file__).parent / "extractor.py"
    memory_path = Path(memory_dir or "./memory")
    memory_path.mkdir(parents=True, exist_ok=True)

    subprocess.Popen(
        ["python", str(extractor),
         "--session-file", str(tmp_file),
         "--memory-dir", str(memory_path),
         "--session-id", session_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "message": "Memory extraction started in background"}
