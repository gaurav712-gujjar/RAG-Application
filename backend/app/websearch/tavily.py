"""Web search through the Tavily API (reliable from cloud servers). Needs TAVILY_API_KEY."""
import logging

import httpx

from app.websearch.ddg import WebResult

logger = logging.getLogger(__name__)
_URL = "https://api.tavily.com/search"


async def tavily_search(
    query: str, api_key: str, max_results: int = 5, timeout: float = 8.0,
    client: httpx.AsyncClient | None = None,
) -> list[WebResult]:
    """Raises on HTTP/network errors so the caller can fall back to another provider."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        r = await client.post(
            _URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "search_depth": "basic", "max_results": max_results, "topic": "general"},
        )
        r.raise_for_status()
        out = []
        for x in r.json().get("results", []):
            url = x.get("url") or ""
            if url.startswith("http"):
                out.append(WebResult(title=(x.get("title") or url)[:150], url=url, snippet=(x.get("content") or "")[:500]))
        return out
    finally:
        if own:
            await client.aclose()
