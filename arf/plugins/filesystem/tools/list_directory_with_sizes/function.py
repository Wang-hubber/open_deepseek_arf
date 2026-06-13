"""list_directory_with_sizes tool — directory listing with file sizes."""

import os


def _format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    if size == 0:
        return "0 B"
    i = 0
    fsize = float(size)
    while fsize >= 1024 and i < len(units) - 1:
        fsize /= 1024
        i += 1
    return f"{fsize:.2f} {units[i]}"


async def execute(path: str, sortBy: str = "name", **kwargs) -> dict:
    if not os.path.isdir(path):
        return {"ok": False, "error": f"Not a directory: {path}"}

    try:
        detailed = []
        with os.scandir(path) as it:
            for entry in it:
                try:
                    st = entry.stat()
                    detailed.append({
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "size": st.st_size if not entry.is_dir() else 0,
                    })
                except OSError:
                    detailed.append({
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "size": 0,
                    })
    except OSError as e:
        return {"ok": False, "error": str(e)}

    if sortBy == "size":
        detailed.sort(key=lambda x: x["size"], reverse=True)
    else:
        detailed.sort(key=lambda x: x["name"])

    entries = []
    for d in detailed:
        prefix = "[DIR]" if d["is_dir"] else "[FILE]"
        size_str = "" if d["is_dir"] else _format_size(d["size"]).rjust(10)
        entries.append(f"{prefix} {d['name']:<30} {size_str}")

    total_files = sum(1 for d in detailed if not d["is_dir"])
    total_dirs = sum(1 for d in detailed if d["is_dir"])
    total_size = sum(d["size"] for d in detailed)

    entries.append("")
    entries.append(f"Total: {total_files} files, {total_dirs} directories")
    entries.append(f"Combined size: {_format_size(total_size)}")

    return {
        "ok": True,
        "entries": entries,
        "total_files": total_files,
        "total_dirs": total_dirs,
        "combined_size": total_size,
        "combined_size_human": _format_size(total_size),
    }
