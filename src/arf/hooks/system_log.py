"""system_log hook -- logs hook events to workspace/memory/hook_events.log.

Usage:
    python -m arf.hooks.system_log

Context:
    Environment: ARF_HOOK_EVENT, ARF_HOOK_WORKSPACE, ARF_HOOK_SESSION_ID,
                 ARF_HOOK_TOOL_NAME, ARF_HOOK_TOOL_INPUT, ARF_HOOK_TOOL_OUTPUT
    Stdin: JSON with full event payload
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    event = os.environ.get("ARF_HOOK_EVENT", "Unknown")
    workspace = Path(os.environ.get("ARF_HOOK_WORKSPACE", "."))
    session_id = os.environ.get("ARF_HOOK_SESSION_ID", "")
    tool_name = os.environ.get("ARF_HOOK_TOOL_NAME", "")

    # Ensure log directory exists
    log_dir = workspace / "memory"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "hook_events.log"

    ts = datetime.now(timezone.utc).isoformat()

    entry = {
        "ts": ts,
        "event": event,
        "session_id": session_id,
    }
    if tool_name:
        entry["tool"] = tool_name

    # Include additional context from environment
    for key in ("MODEL", "TURN", "DURATION_MS", "FINISH_REASON",
                 "TOOL_CATEGORY", "STATUS",
                 "PROMPT_TOKENS", "COMPLETION_TOKENS", "TOTAL_TOKENS"):
        val = os.environ.get(f"ARF_HOOK_{key}", "")
        if val:
            try:
                entry[key.lower()] = int(val) if val.isdigit() else float(val)
            except (ValueError, TypeError):
                entry[key.lower()] = val

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"system_log hook error: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
