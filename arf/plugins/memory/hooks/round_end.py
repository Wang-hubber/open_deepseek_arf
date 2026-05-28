"""round_end hook — check trigger interval and dispatch memory extraction."""
import os
import json
import subprocess
import sys
from pathlib import Path


def main():
    config_json = os.environ.get("ARF_PLUGIN_CONFIG", "{}")
    config = json.loads(config_json)

    interval = config.get("interval", 10)
    current_round = int(os.environ.get("ARF_ROUND", 0))
    memory_dir = os.environ.get(
        "ARF_MEMORY_DIR", config.get("memory_dir", "./memory")
    )
    session_id = os.environ.get("ARF_SESSION_ID", "default")

    # Check trigger: fire every N rounds
    if current_round <= 0 or current_round % interval != 0:
        sys.exit(0)

    # Read session messages from FileStateStore
    state_file = Path(memory_dir) / "state" / f"{session_id}.json"
    if not state_file.exists():
        sys.exit(0)

    state = json.loads(state_file.read_text())
    messages = state.get("messages", [])
    if not messages:
        sys.exit(0)

    # Write messages to temp file for subprocess
    tmp_file = Path(memory_dir) / "state" / f"extract_{session_id}.json"
    tmp_file.write_text(json.dumps(messages, ensure_ascii=False))

    # Dispatch extractor subprocess
    extractor = (
        Path(__file__).parent.parent
        / "tools" / "memory_extract" / "extractor.py"
    )
    subprocess.Popen(
        ["python", str(extractor),
         "--session-file", str(tmp_file),
         "--memory-dir", str(memory_dir),
         "--session-id", session_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
