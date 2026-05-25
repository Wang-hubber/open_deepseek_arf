"""ARF Default Assistant entry point.

App declares its root directory and framework derives all standard paths from it.
Both cli.py and server.py consume this module — no scattered path strings.
"""
from pathlib import Path
from arf.agent import AppContext

APP_ROOT = Path(__file__).parent.resolve()

app_context = AppContext(root=APP_ROOT)
