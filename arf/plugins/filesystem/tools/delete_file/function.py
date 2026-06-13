"""delete_file tool — delete files and directories."""

import os
import shutil


async def execute(path: str, recursive: bool = False, **kwargs) -> dict:
    if not os.path.exists(path):
        return {"ok": False, "error": f"Path does not exist: {path}"}

    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.unlink(path)
        elif os.path.isdir(path):
            if recursive:
                shutil.rmtree(path)
            else:
                os.rmdir(path)  # fails if not empty
    except OSError as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "path": path, "recursive": recursive}
