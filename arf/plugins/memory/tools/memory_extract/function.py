"""User-triggered memory extraction via tool call."""
import json
import subprocess
from pathlib import Path


async def execute(session_id: str = "", memory_dir: str = "",
                  state_dir: str = "", force: bool = False) -> dict:
    """Read session state from FileStateStore, spawn extractor subprocess.

    Uses *state_dir* (not _state_store) because _-prefixed DI objects
    are stripped at the MCP boundary and never reach tool functions.
    """
    state_path = Path(state_dir or "./data/state")
    state_file = state_path / f"{session_id}.json"
    if not state_file.exists():
        return {"ok": False, "error": f"No session state found: {state_file}"}

    state = json.loads(state_file.read_text())
    messages = state.get("messages", [])
    if not messages:
        return {"ok": False, "error": "No messages to extract from"}

    memory_path = Path(memory_dir or "./data/memory")
    memory_path.mkdir(parents=True, exist_ok=True)
    tmp_dir = memory_path / "state"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"extract_{session_id}.json"
    tmp_file.write_text(json.dumps(messages, ensure_ascii=False))

    extractor = Path(__file__).parent / "extractor.py"
    subprocess.Popen(
        ["python", str(extractor),
         "--session-file", str(tmp_file),
         "--memory-dir", str(memory_path),
         "--session-id", session_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "message": "Memory extraction started in background"}
