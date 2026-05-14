"""Built-in hook scripts -- subprocess hooks for the ARF session lifecycle.

Each hook reads context from environment variables (ARF_HOOK_*) and
stdin JSON, then writes results to stdout (JSON) and signals intent
via exit code (0=continue, 1=block, 2=inject).
"""
