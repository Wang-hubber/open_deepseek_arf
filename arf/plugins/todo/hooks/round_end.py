"""round_end hook — remind agent if TODO hasn't been updated in N rounds."""
import json
import os
import sys
from pathlib import Path


def main():
    runtime_raw = os.environ.get("ARF_RUNTIME", "{}")
    runtime = json.loads(runtime_raw)

    plugin_config_raw = os.environ.get("ARF_PLUGIN_CONFIG", "{}")
    plugin_config = json.loads(plugin_config_raw)

    interval = plugin_config.get("reminder_interval", 5)
    current_round = runtime.get("interaction_round", 0)
    workspace_dir = runtime.get("workspace_dir", "./workspace")

    if current_round <= 0:
        sys.exit(0)

    tasks_file = Path(workspace_dir) / "default" / "tasks.json"
    if not tasks_file.exists():
        sys.exit(0)

    try:
        data = json.loads(tasks_file.read_text())
        last_updated = data.get("last_updated_round", 0)
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    rounds_since = current_round - last_updated
    if rounds_since >= interval:
        print(
            f"[TODO Reminder] {rounds_since} rounds since last TODO update. "
            f"Please check task progress and call todo(action=\"update\", id=\"...\", status=\"...\") "
            f"to sync status, or todo(action=\"list\") to review tasks."
        )
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
