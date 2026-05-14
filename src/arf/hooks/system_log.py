"""system_log hook -- logs hook events to workspace/memory/hook_events.log.

Usage:
    python -m arf.hooks.system_log

Context:
    Environment: ARF_HOOK_EVENT, ARF_HOOK_WORKSPACE, ARF_HOOK_SESSION_ID,
                 ARF_HOOK_TOOL_NAME, ARF_HOOK_TOOL_INPUT, ARF_HOOK_TOOL_OUTPUT
    Stdin: JSON with full event payload
"""

import json
import logging
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

    parts = [f"[{ts}] event={event}"]
    if session_id:
        parts.append(f"session={session_id}")
    if tool_name:
        parts.append(f"tool={tool_name}")

    line = " ".join(parts)

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # non-critical

    # Always succeed
    sys.exit(0)


if __name__ == "__main__":
    main()
