"""create_directory tool — recursive directory creation."""

import os


async def execute(path: str, **kwargs) -> dict:
    existed = os.path.isdir(path)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "path": path, "created": not existed}
