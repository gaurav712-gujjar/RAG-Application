import asyncio

import httpx

from app.websearch import search as search_mod
from app.websearch.ddg import WebResult
from app.websearch.tavily import tavily_search


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_tavily_parses_results_and_sends_bearer_key():
    seen = {}

    def handler(req: httpx.Request):
        import json
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"results": [
            {"title": "A", "url": "https://a.test", "content": "alpha"},
            {"title": "bad", "url": "javascript:x", "content": "x"},
        ]})

    out = asyncio.run(tavily_search("jaipur", "tvly-k", 3, client=_client(handler)))
    assert [r.url for r in out] == ["https://a.test"] and out[0].snippet == "alpha"
    assert seen["auth"] == "Bearer tvly-k" and seen["body"]["max_results"] == 3


def test_dispatcher_uses_tavily_when_key_set(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")

    async def tav(q, key, n):
        return [WebResult("T", "https://t.test", "s")]

    async def ddg(*a, **k):
        raise AssertionError("must not be called")

    monkeypatch.setattr(search_mod, "tavily_search", tav)
    monkeypatch.setattr(search_mod, "ddg_search", ddg)
    assert asyncio.run(search_mod.web_search("q"))[0].url == "https://t.test"


def test_dispatcher_falls_back_to_ddg_on_tavily_error_or_no_key(monkeypatch):
    async def boom(q, key, n):
        raise RuntimeError("401")

    async def ddg(q, **k):
        return [WebResult("D", "https://d.test", "s")]

    monkeypatch.setattr(search_mod, "tavily_search", boom)
    monkeypatch.setattr(search_mod, "ddg_search", ddg)
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    assert asyncio.run(search_mod.web_search("q"))[0].url == "https://d.test"
    monkeypatch.delenv("TAVILY_API_KEY")
    assert asyncio.run(search_mod.web_search("q"))[0].url == "https://d.test"
