"""write_file tool — atomic file write."""

import os
import tempfile


async def execute(path: str, content: str, **kwargs) -> dict:
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    try:
        # Try exclusive creation first
        with open(path, "x", encoding="utf-8") as f:
            f.write(content)
    except FileExistsError:
        # Atomic overwrite: write to temp then rename
        tmpdir = os.path.dirname(path) or "."
        try:
            fd, tmpname = tempfile.mkstemp(dir=tmpdir, prefix=".write_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmpname, path)
            except Exception:
                try:
                    os.unlink(tmpname)
                except OSError:
                    pass
                raise
        except OSError as e:
            return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "path": path, "bytes_written": len(content.encode("utf-8"))}
