"""read_media_file tool — read image/audio as base64."""

import base64
import os

MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}

MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


async def execute(path: str, **kwargs) -> dict:
    if not os.path.isfile(path):
        return {"ok": False, "error": f"Not a file: {path}"}

    ext = os.path.splitext(path)[1].lower()
    mime_type = MIME_TYPES.get(ext, "application/octet-stream")

    try:
        size = os.path.getsize(path)
        if size > MAX_SIZE_BYTES:
            return {"ok": False, "error": f"File too large: {size} bytes (max {MAX_SIZE_BYTES})"}

        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return {"ok": False, "error": str(e)}

    encoded = base64.b64encode(data).decode("ascii")

    if mime_type.startswith("image/"):
        media_type = "image"
    elif mime_type.startswith("audio/"):
        media_type = "audio"
    else:
        media_type = "blob"

    return {
        "ok": True,
        "type": media_type,
        "data": encoded,
        "mimeType": mime_type,
        "size_bytes": size,
    }
