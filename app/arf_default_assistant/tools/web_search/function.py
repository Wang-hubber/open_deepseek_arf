"""web_search -- DuckDuckGo-based web search (stdlib only, no API key needed)."""
import html
import json
import re
import sys
import traceback
import urllib.parse
import urllib.request
from datetime import datetime

DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"


async def execute(query: str, max_results: int = 10) -> dict:
    try:
        if not query or not isinstance(query, str) or not query.strip():
            return {"error": "query must be a non-empty string"}
        query = query.strip()
        if not isinstance(max_results, int) or max_results < 1:
            max_results = 10
        max_results = min(max_results, 20)

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

        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8",
                errors="replace",
            )

        results = _parse_results(raw, max_results)
        return {"ok": True, "query": query, "results": results, "count": len(results)}

    except Exception as exc:
        return {"error": str(exc), "detail": type(exc).__name__}


def _parse_results(html_text: str, max_results: int) -> list:
    results = []
    blocks = re.split(r'<div class="[^"]*result[^"]*">', html_text)[1:]

    for block in blocks:
        if len(results) >= max_results:
            break

        link_match = re.search(
            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            block, re.DOTALL,
        )
        if not link_match:
            continue

        url = html.unescape(link_match.group(1).strip())
        title = _strip_html(link_match.group(2)).strip()

        snippet_match = re.search(
            r'<[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div|span)>',
            block, re.DOTALL,
        )
        snippet = ""
        if snippet_match:
            snippet = _strip_html(snippet_match.group(1)).strip()

        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text
