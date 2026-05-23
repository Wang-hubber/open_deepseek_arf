"""web_fetch -- async HTTP GET with browser-like headers."""
import re
import traceback
import urllib.error
import urllib.request
from datetime import datetime


async def execute(url: str, timeout: int = 15) -> dict:
    try:
        if not url or not isinstance(url, str):
            return {"error": "url must be a non-empty string"}
        if not url.startswith(("http://", "https://")):
            return {"error": f"url must start with http:// or https://, got: {url[:50]}"}
        if not isinstance(timeout, int) or timeout < 1:
            timeout = 15

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8",
                errors="replace",
            )
            status = resp.status
            content_type = resp.headers.get_content_type()

        max_bytes = 200 * 1024
        if len(content) > max_bytes:
            content = content[:max_bytes] + f"\n\n[TRUNCATED: {len(content) - max_bytes} more bytes]"

        content = re.sub(r"\n\s*\n\s*\n+", "\n\n", content)

        return {
            "ok": True,
            "status": status,
            "content_type": content_type,
            "content": content,
        }

    except urllib.error.HTTPError as exc:
        return {"error": str(exc), "detail": "HTTPError", "status": exc.code}
    except urllib.error.URLError as exc:
        return {"error": str(exc), "detail": "URLError"}
    except Exception as exc:
        return {"error": str(exc), "detail": type(exc).__name__}
