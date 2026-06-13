"""get_file_info tool — file/directory metadata."""

import os
import time


async def execute(path: str, **kwargs) -> dict:
    try:
        st = os.stat(path)
    except OSError as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "size": st.st_size,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_ctime)),
        "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
        "accessed": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_atime)),
        "isDirectory": os.path.isdir(path),
        "isFile": os.path.isfile(path),
        "permissions": oct(st.st_mode)[-3:],
    }
