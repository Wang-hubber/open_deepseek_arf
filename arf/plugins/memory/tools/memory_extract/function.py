"""User-triggered memory extraction via tool call."""
import json
import subprocess
from pathlib import Path


async def execute(session_id: str = "", memory_dir: str = "",
                  data_dir: str = "", force: bool = False) -> dict:
    """Read session state from FileStateStore, spawn extractor subprocess.

    *session_id* is injected by the engine — not declared as a tool param.
    """
    data_path = Path(data_dir or "./data")
    state_file = data_path / session_id / "state" / f"{session_id}.json"
    if not state_file.exists():
        return {"ok": False, "error": f"No session state found: {state_file}"}

    state = json.loads(state_file.read_text(encoding="utf-8"))
    messages = state.get("messages", [])
    if not messages:
        return {"ok": False, "error": "No messages to extract from"}

    memory_path = Path(memory_dir or "./data/memory")
    memory_path.mkdir(parents=True, exist_ok=True)
    tmp_dir = memory_path / "state"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"extract_{session_id}.json"
    tmp_file.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")

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
