"""move_file tool — move/rename files and directories."""
import os
import shutil


async def execute(source: str, destination: str, **kwargs) -> dict:
    if not os.path.exists(source):
        return {"ok": False, "error": f"Source does not exist: {source}"}

    if os.path.exists(destination):
        return {"ok": False, "error": f"Destination already exists: {destination}"}

    dest_parent = os.path.dirname(destination)
    if dest_parent and not os.path.isdir(dest_parent):
        os.makedirs(dest_parent, exist_ok=True)

    try:
        shutil.move(source, destination)
    except OSError as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "source": source, "destination": destination}
