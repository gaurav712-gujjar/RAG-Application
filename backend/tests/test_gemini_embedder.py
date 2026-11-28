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

    e = GeminiEmbedder("k", dim=2, client=_client(handler), batch_size=100)
    vecs = asyncio.run(e.embed(["a"] * 150))
    assert len(vecs) == 150
    assert sorted(c[1] for c in calls) == [50, 100]
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


def test_batches_run_concurrently_but_never_above_the_limit_and_keep_order():
    import json

    state = {"now": 0, "peak": 0}

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req):
            body = json.loads(req.content)
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.05)
            state["now"] -= 1
            # echo each text's number back as the vector so order can be checked
            vals = [{"values": [float(r["content"]["parts"][0]["text"]), 1.0]} for r in body["requests"]]
            return httpx.Response(200, json={"embeddings": vals})

    e = GeminiEmbedder("k", dim=2, client=httpx.AsyncClient(transport=Slow()), batch_size=10, concurrency=3)
    texts = [str(i) for i in range(95)]  # 10 batches
    vecs = asyncio.run(e.embed(texts))
    assert state["peak"] == 3
    ratio = [v[0] / v[1] for v in vecs]
    assert [round(x) for x in ratio] == list(range(95))  # same order as the input
