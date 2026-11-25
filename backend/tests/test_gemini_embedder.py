import asyncio
import math

import httpx

from app.embeddings.gemini import GeminiEmbedder


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_batches_normalizes_and_sends_key():
    calls = []

    def handler(req: httpx.Request):
        import json
        body = json.loads(req.content)
        calls.append((req.headers["x-goog-api-key"], len(body["requests"]), body["requests"][0]["outputDimensionality"]))
        return httpx.Response(200, json={"embeddings": [{"values": [3.0, 4.0]} for _ in body["requests"]]})

    e = GeminiEmbedder("k", dim=2, client=_client(handler))
    vecs = asyncio.run(e.embed(["a"] * 150))
    assert len(vecs) == 150
    assert [c[1] for c in calls] == [100, 50]
    assert calls[0][0] == "k" and calls[0][2] == 2
    assert math.isclose(sum(x * x for x in vecs[0]), 1.0)


def test_retries_on_429():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"embeddings": [{"values": [1.0, 0.0]}]})

    e = GeminiEmbedder("k", dim=2, client=_client(handler))
    assert asyncio.run(e.embed(["x"])) == [[1.0, 0.0]]
    assert n["i"] == 2


def test_empty_input():
    e = GeminiEmbedder("k", dim=2, client=_client(lambda r: httpx.Response(500)))
    assert asyncio.run(e.embed([])) == []
