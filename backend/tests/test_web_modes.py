from app.llm.prompts import NO_WEB_RESULTS, NOT_FOUND
from app.main import app
from app.websearch.ddg import WebResult
from tests.helpers import parse_sse, signup, upload

DOC = b"Refunds are processed within 14 days. The refund policy covers all purchases."
OFF_TOPIC = "quantum chromodynamics lattice gauge"


async def _setup(ctx, email="web@example.com"):
    headers = await signup(ctx, email)
    await upload(ctx, headers, "policy.txt", DOC)
    return headers


def _fake_web(monkeypatch, results, calls=None):
    async def fake(query, **kw):
        if calls is not None:
            calls.append(query)
        return results

    monkeypatch.setattr("app.api.chat.web_search", fake)


async def test_docs_mode_never_calls_web_and_offers_it(ctx, monkeypatch):
    headers = await _setup(ctx)
    calls = []
    _fake_web(monkeypatch, [WebResult("T", "https://x.test", "s")], calls)
    r = await ctx.post("/chat", headers=headers, json={"question": OFF_TOPIC})
    ev = dict(parse_sse(r.text))
    assert ev["token"]["t"] == NOT_FOUND and ev["done"]["web_available"] is True
    assert calls == []


async def test_docs_web_falls_back_to_web_when_nothing_found(ctx, monkeypatch):
    headers = await _setup(ctx)
    _fake_web(monkeypatch, [WebResult("Lattice QCD", "https://qcd.test/a", "A lattice method.")])
    r = await ctx.post("/chat", headers=headers, json={"question": OFF_TOPIC, "mode": "docs_web"})
    events = parse_sse(r.text)
    sources = [d for e, d in events if e == "sources"][-1]
    assert sources[0]["kind"] == "web" and sources[0]["url"] == "https://qcd.test/a"
    done = dict(events)["done"]
    assert done["answer_source"] == "web" and done["model"] is not None
    assert "Web results:" in app.state.llm.last_messages[-1]["content"]


async def test_docs_web_prefers_documents_when_found(ctx, monkeypatch):
    headers = await _setup(ctx)
    calls = []
    _fake_web(monkeypatch, [WebResult("T", "https://x.test", "s")], calls)
    r = await ctx.post("/chat", headers=headers, json={"question": "What is the refund policy?", "mode": "docs_web"})
    assert dict(parse_sse(r.text))["done"]["answer_source"] == "docs"
    assert calls == []


async def test_web_mode_skips_documents(ctx, monkeypatch):
    headers = await _setup(ctx)
    _fake_web(monkeypatch, [WebResult("T", "https://x.test", "s")])
    r = await ctx.post("/chat", headers=headers, json={"question": "What is the refund policy?", "mode": "web"})
    done = dict(parse_sse(r.text))["done"]
    assert done["answer_source"] == "web" and done["retrieval_ms"] == 0


async def test_web_without_results_skips_llm(ctx, monkeypatch):
    headers = await _setup(ctx)
    _fake_web(monkeypatch, [])
    app.state.llm.calls = 0
    r = await ctx.post("/chat", headers=headers, json={"question": OFF_TOPIC, "mode": "web"})
    assert dict(parse_sse(r.text))["token"]["t"] == NO_WEB_RESULTS
    assert app.state.llm.calls == 0


async def test_doc_ids_limits_search_to_chosen_documents(ctx):
    headers = await _setup(ctx)
    r = await ctx.post("/chat", headers=headers, json={"question": "What is the refund policy?", "doc_ids": ["nope"]})
    assert dict(parse_sse(r.text))["token"]["t"] == NOT_FOUND
