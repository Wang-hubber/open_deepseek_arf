#!/usr/bin/env python3
"""Post-tool-exec hook: detect resource changes -> trigger hot reload."""
import os
import sys

TOOL = os.environ.get("ARF_TOOL_NAME", "")
if TOOL in ("resource_scaffold", "file_writer", "resource_clone", "tool_generator", "skill_generator"):
    # Trigger reload via API
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/reload", timeout=3)
        print(f"[self_evolve] Reload triggered after {TOOL}", file=sys.stderr)
    except Exception as e:
        print(f"[self_evolve] Could not reach server for reload: {e}", file=sys.stderr)
sys.exit(0)
