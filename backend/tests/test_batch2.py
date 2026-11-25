from app.core.config import get_settings
from app.llm.smalltalk import smalltalk
from app.main import app
from tests.helpers import parse_sse, signup, upload

DOC = b"Refunds are processed within 14 days. The refund policy covers all purchases."


# ---- greetings ------------------------------------------------------------------------------
def test_smalltalk_detects_greetings_in_three_languages_and_ignores_questions():
    assert "Ask me anything" in smalltalk("hi")
    assert "नमस्ते" in smalltalk("नमस्ते")
    assert "Namaste" in smalltalk("namaste")
    assert "welcome" in smalltalk("Thanks!").lower()
    assert smalltalk("hi, what is the refund policy?") is None
    assert smalltalk("tell me about jaipur") is None
    assert smalltalk("x" * 60) is None


async def test_greeting_skips_search_web_and_llm(ctx, monkeypatch):
    headers = await signup(ctx, "hi@example.com")
    called = []

    async def boom(*a, **k):
        called.append(1)
        return []

    monkeypatch.setattr("app.api.chat.web_search", boom)
    app.state.llm.calls = 0
    r = await ctx.post("/chat", headers=headers, json={"question": "hi", "mode": "docs_web"})
    ev = dict(parse_sse(r.text))
    assert "Ask me anything" in ev["token"]["t"] and ev["done"]["model"] is None
    assert called == [] and app.state.llm.calls == 0


# ---- source passages --------------------------------------------------------------------------
async def test_document_sources_carry_the_passage_text(ctx):
    headers = await signup(ctx, "src@example.com")
    await upload(ctx, headers, "policy.txt", DOC)
    r = await ctx.post("/chat", headers=headers, json={"question": "What is the refund policy?"})
    src = dict(parse_sse(r.text))["sources"][0]
    assert "Refunds are processed" in src["text"]


# ---- server chat history ------------------------------------------------------------------
async def test_threads_save_list_update_delete_and_isolation(ctx):
    a = await signup(ctx, "a@example.com")
    b = await signup(ctx, "b@example.com")
    tid = "11111111-2222-3333-4444-555555555555"
    msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

    r = await ctx.put(f"/threads/{tid}", headers=a, json={"title": "First", "messages": msgs})
    assert r.status_code == 200
    r = await ctx.put(f"/threads/{tid}", headers=a, json={"title": "Renamed", "messages": msgs[:1]})
    listed = (await ctx.get("/threads", headers=a)).json()
    assert [t["title"] for t in listed] == ["Renamed"] and len(listed[0]["messages"]) == 1

    assert (await ctx.get("/threads", headers=b)).json() == []
    assert (await ctx.put(f"/threads/{tid}", headers=b, json={"title": "x", "messages": []})).status_code == 404
    await ctx.delete(f"/threads/{tid}", headers=b)  # someone else's chat: nothing happens
    assert len((await ctx.get("/threads", headers=a)).json()) == 1

    assert (await ctx.delete(f"/threads/{tid}", headers=a)).status_code == 204
    assert (await ctx.get("/threads", headers=a)).json() == []


async def test_thread_too_large_is_rejected(ctx):
    h = await signup(ctx, "big@example.com")
    big = [{"role": "user", "content": "x" * 5000} for _ in range(50)]
    r = await ctx.put("/threads/aaaaaaaa-bbbb", headers=h, json={"title": "t", "messages": big})
    assert r.status_code == 413


# ---- suggestions ----------------------------------------------------------------------------
async def test_suggestions_are_written_once_then_stored(ctx):
    h = await signup(ctx, "sug@example.com")
    up = await upload(ctx, h, "policy.txt", DOC)
    doc_id = up.json()["id"]
    app.state.llm.rewrite_result = "1. What is the refund window?\n- How long do refunds take?\nOK"
    r1 = await ctx.get(f"/documents/{doc_id}/suggestions", headers=h)
    assert r1.json()["questions"] == ["What is the refund window?", "How long do refunds take?"]
    n = app.state.llm.complete_calls
    r2 = await ctx.get(f"/documents/{doc_id}/suggestions", headers=h)
    assert r2.json() == r1.json() and app.state.llm.complete_calls == n  # stored, no second LLM call


async def test_suggestions_other_users_document_is_404(ctx):
    owner = await signup(ctx, "o@example.com")
    other = await signup(ctx, "p@example.com")
    doc_id = (await upload(ctx, owner, "policy.txt", DOC)).json()["id"]
    assert (await ctx.get(f"/documents/{doc_id}/suggestions", headers=other)).status_code == 404


# ---- admin ------------------------------------------------------------------------------------
async def test_admin_usage_is_off_without_token_and_needs_it_when_on(ctx, monkeypatch):
    import app.api.admin as admin

    monkeypatch.setattr(admin.settings, "admin_token", "")
    assert (await ctx.get("/admin/usage")).status_code == 404

    monkeypatch.setattr(admin.settings, "admin_token", "s3cret")
    assert (await ctx.get("/admin/usage")).status_code == 401
    assert (await ctx.get("/admin/usage", headers={"X-Admin-Token": "wrong"})).status_code == 401

    h = await signup(ctx, "adm@example.com")
    await ctx.post("/chat", headers=h, json={"question": "What is the refund policy?"})
    r = await ctx.get("/admin/usage", headers={"X-Admin-Token": "s3cret"})
    body = r.json()
    assert r.status_code == 200 and body["users"] == 1 and "per_user" in body
