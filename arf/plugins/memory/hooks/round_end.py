"""round_end hook — check trigger interval and dispatch memory extraction."""
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    runtime_raw = os.environ.get("ARF_RUNTIME", "{}")
    runtime = json.loads(runtime_raw)

    plugin_config_raw = os.environ.get("ARF_PLUGIN_CONFIG", "{}")
    plugin_config = json.loads(plugin_config_raw)

    interval = plugin_config.get("interval", 10)
    current_round = runtime.get("interaction_round", 0)
    memory_dir = runtime.get("memory_dir", "./memory")
    state_dir = runtime.get("state_dir", "./data/state")
    session_id = runtime.get("session_id", "default")
    python_exe = runtime.get("python_executable", sys.executable)

    # Check trigger: fire every N rounds
    if current_round <= 0 or current_round % interval != 0:
        sys.exit(0)

    # Read session messages from engine's state store (data/state/),
    # NOT from memory_dir (which stores memory.md, not session checkpoints)
    state_file = Path(state_dir) / f"{session_id}.json"
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
    result = subprocess.run(
        [python_exe, str(extractor),
         "--session-file", str(tmp_file),
         "--memory-dir", str(memory_dir),
         "--session-id", session_id],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"Extractor failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
