"""One entry point for web search: Tavily when TAVILY_API_KEY is set, DuckDuckGo as the fallback."""
import logging
import os

from app.core.config import get_settings

from app.websearch.ddg import WebResult, web_search as ddg_search
from app.websearch.tavily import tavily_search

logger = logging.getLogger(__name__)


async def web_search(query: str, max_results: int = 5, region: str = "in-en") -> list[WebResult]:
    key = (os.getenv("TAVILY_API_KEY") or get_settings().tavily_api_key or "").strip()
    if key:
        try:
            results = await tavily_search(query, key, max_results)
            if results:
                return results
        except Exception as exc:  # never log the key; the exception text does not contain it
            logger.warning("Tavily search failed (%s); falling back to DuckDuckGo", type(exc).__name__)
    return await ddg_search(query, max_results=max_results, region=region)
