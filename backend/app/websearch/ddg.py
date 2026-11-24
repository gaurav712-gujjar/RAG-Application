"""Web search through DuckDuckGo (the `ddgs` package, no API key). Fails soft: any error -> []."""
import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


_BACKENDS = "duckduckgo,brave,yahoo,mojeek,startpage"


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str


def _search_sync(query: str, max_results: int, region: str) -> list[WebResult]:
    from ddgs import DDGS  # imported lazily so the app starts even if the package is missing

    # Explicit engines: "auto" asks wikipedia and grokipedia first, which return unrelated pages
    # (e.g. "summer vacation ideas" for a person's name) and fail on the world-wide region.
    raw = DDGS().text(query, region=region, max_results=max_results, backend=_BACKENDS) or []
    out = []
    for r in raw:
        url = r.get("href") or r.get("url") or ""
        if url.startswith("http"):
            out.append(WebResult(title=(r.get("title") or url)[:150], url=url, snippet=(r.get("body") or "")[:500]))
    return out


async def web_search(query: str, max_results: int = 5, region: str = "in-en", timeout: float = 8.0) -> list[WebResult]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_search_sync, query, max_results, region), timeout)
    except Exception:  # timeout, rate limit, network, missing package
        logger.warning("web search failed", exc_info=True)
        return []
