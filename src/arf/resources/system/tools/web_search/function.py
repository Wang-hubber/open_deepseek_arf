"""web_search -- DuckDuckGo-based web search (stdlib only, no API key needed).

Returns title, snippet, and URL for each result.
Users can override by placing a custom web_search in their workspace.
"""
import html
import re
import traceback
import urllib.parse
import urllib.request
from datetime import datetime


DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"


def _log(level: str, message: str, **extra) -> None:
    import json, sys
    entry = {
        "tool": "web_search",
        "ts": datetime.now().isoformat(),
        "level": level,
        "msg": message,
        **extra,
    }
    print(json.dumps(entry, ensure_ascii=False), file=sys.stderr)


def execute(query, max_results=10, timeout=15):
    _log("INFO", "execute called", query=query, max_results=max_results)
    try:
        # ---- input validation ----
        if not query or not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        query = query.strip()
        if not isinstance(max_results, int) or max_results < 1:
            max_results = 10
        max_results = min(max_results, 20)

        # ---- search ----
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request(
            DUCKDUCKGO_HTML,
            data=data,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8",
                errors="replace",
            )

        results = _parse_results(raw, max_results)

        _log("INFO", "execute succeeded", result_count=len(results))
        return {
            "ok": True,
            "query": query,
            "results": results,
            "count": len(results),
        }

    except Exception as exc:
        _log("ERROR", str(exc),
             traceback=traceback.format_exc().split("\n")[-3:],
             inputs={"query": query, "max_results": max_results})
        return {"error": str(exc), "detail": type(exc).__name__}


def _parse_results(html_text, max_results):
    """Parse DuckDuckGo HTML search results page."""
    results = []

    # Each result block: <div class="result"> ... </div>  </div>  </div>
    # Title: <a class="result__a" href="...">Title</a>
    # Snippet: <a class="result__snippet">...</a>

    blocks = re.split(r'<div class="[^"]*result[^"]*">', html_text)[1:]

    for block in blocks:
        if len(results) >= max_results:
            break

        # Extract URL and title from result__a
        link_match = re.search(
            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            block, re.DOTALL,
        )
        if not link_match:
            continue

        url = html.unescape(link_match.group(1).strip())
        title = _strip_html(link_match.group(2)).strip()

        # Extract snippet
        snippet_match = re.search(
            r'<[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div|span)>',
            block, re.DOTALL,
        )
        snippet = ""
        if snippet_match:
            snippet = _strip_html(snippet_match.group(1)).strip()

        if title and url:
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
            })

    return results


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text
