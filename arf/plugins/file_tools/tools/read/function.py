"""read — read file contents with line numbers."""
import base64
from pathlib import Path

WORKSPACE = Path("workspace/default")
MAX_LINES = 2000
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


async def execute(
    file_path: str,
    offset: int = 1,
    limit: int | None = None,
) -> dict:
    p = WORKSPACE / file_path
    try:
        if not p.exists():
            return {"ok": False, "error": f"File not found: {file_path}"}
        if p.is_dir():
            return {"ok": False, "error": f"Path is a directory: {file_path}"}

        suffix = p.suffix.lower()

        # Image files — return base64 data URI
        if suffix in IMAGE_EXTENSIONS:
            mime_map = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
            }
            mime = mime_map.get(suffix, "application/octet-stream")
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            return {
                "ok": True,
                "type": "image",
                "mime_type": mime,
                "data_uri": f"data:{mime};base64,{data}",
                "size_bytes": p.stat().st_size,
            }

        # Text files — read with offset/limit
        raw = p.read_text(encoding="utf-8")
        lines = raw.split("\n")
        total_lines = len(lines)

        start = max(offset - 1, 0)
        if start >= total_lines:
            return {"ok": True, "content": "", "lines_read": 0, "total_lines": total_lines}

        end = total_lines
        if limit is not None:
            end = min(start + limit, total_lines)

        if end - start > MAX_LINES:
            return {
                "ok": False,
                "error": f"Requested range ({end - start} lines) exceeds max ({MAX_LINES}). "
                         f"File has {total_lines} lines. Use offset/limit to narrow the range.",
                "total_lines": total_lines,
            }

        selected = lines[start:end]
        # cat -n style: right-aligned line numbers
        width = len(str(end))
        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i:>{width}}\t{line}")
        content = "\n".join(numbered)

        return {
            "ok": True,
            "content": content,
            "lines_read": len(selected),
            "total_lines": total_lines,
            "offset": offset,
        }
    except UnicodeDecodeError:
        return {"ok": False, "error": f"Cannot read file as UTF-8 text: {file_path}. It may be a binary file."}
    except Exception as e:
        return {"ok": False, "error": str(e)}
