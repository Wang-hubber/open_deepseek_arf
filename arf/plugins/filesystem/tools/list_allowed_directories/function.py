"""list_allowed_directories tool — transparency disclosure of accessible paths."""

import os


async def execute(_workspace: str = "", **kwargs) -> dict:
    directories = []
    if _workspace:
        directories.append(_workspace)
    directories.append(os.getcwd())

    return {"ok": True, "directories": directories}
