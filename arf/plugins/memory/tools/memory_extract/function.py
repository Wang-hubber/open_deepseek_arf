"""User-triggered memory extraction via tool call."""
import json
import subprocess
from pathlib import Path


async def execute(session_id: str = "", memory_dir: str = "",
                  force: bool = False, _state_store=None) -> dict:
    """Read session state from StateStore, spawn extractor subprocess."""
    memory_path = Path(memory_dir or "./data/memory")
    memory_path.mkdir(parents=True, exist_ok=True)

    # Prefer state from injected StateStore, fall back to file lookup
    messages: list[dict] = []
    if _state_store is not None:
        state = await _state_store.get(session_id)
        if state:
            messages = state.get("messages", [])
    if not messages:
        # Fallback: try reading state file from state_dir adjacent to memory_dir
        state_file = memory_path / "state" / f"{session_id}.json"
        if state_file.exists():
            state = json.loads(state_file.read_text())
            messages = state.get("messages", [])

    if not messages:
        return {"ok": False, "error": "No messages to extract from"}

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
