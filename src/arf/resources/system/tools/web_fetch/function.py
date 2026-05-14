import traceback
import urllib.request
import urllib.error
import re
from datetime import datetime


def _log(level: str, message: str, **extra) -> None:
    """Internal logger -- writes structured log entries to stderr."""
    import json, sys
    entry = {"ts": datetime.now().isoformat(), "level": level, "msg": message, **extra}
    print(json.dumps(entry, ensure_ascii=False), file=sys.stderr)


def execute(url: str, timeout: int = 15) -> dict:
    """Fetch a URL with browser-like headers, return plain text content."""
    _log("INFO", "execute called", url=url, timeout=timeout)
    try:
        # ---- input validation ----
        if not url or not isinstance(url, str):
            raise ValueError("url must be a non-empty string")
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"url must start with http:// or https://, got: {url[:50]}")
        if not isinstance(timeout, int) or timeout < 1:
            timeout = 15

        # ---- core logic ----
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

        # Trim to avoid blowing up context (max ~200KB)
        max_bytes = 200 * 1024
        if len(content) > max_bytes:
            content = content[:max_bytes] + f"\n\n[TRUNCATED: {len(content) - max_bytes} more bytes]"

        # Strip excessive whitespace but keep structure
        content = re.sub(r"\n\s*\n\s*\n+", "\n\n", content)

        _log("INFO", "execute succeeded", status=status, content_type=content_type,
             content_len=len(content))
        return {
            "ok": True,
            "status": status,
            "content_type": content_type,
            "content": content,
        }

    except urllib.error.HTTPError as exc:
        _log("ERROR", str(exc), status=exc.code, url=url,
             traceback=traceback.format_exc().split("\n")[-3:])
        return {"error": str(exc), "detail": "HTTPError", "status": exc.code}
    except urllib.error.URLError as exc:
        _log("ERROR", str(exc), reason=str(exc.reason), url=url,
             traceback=traceback.format_exc().split("\n")[-3:])
        return {"error": str(exc), "detail": "URLError"}
    except Exception as exc:
        _log("ERROR", str(exc),
             traceback=traceback.format_exc().split("\n")[-3:],
             inputs={"url": url, "timeout": timeout})
        return {"error": str(exc), "detail": type(exc).__name__}
