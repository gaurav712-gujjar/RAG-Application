from app.main import app
from tests.helpers import upload


async def _demo_headers(ctx):
    r = await ctx.post("/auth/demo")
    assert r.status_code == 200
    await app.state.demo_task  # sample documents are indexed in the background
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_demo_has_sample_documents_and_is_read_only(ctx):
    h = await _demo_headers(ctx)
    docs = (await ctx.get("/documents", headers=h)).json()
    assert len(docs) == 4 and all(d["status"] == "ready" for d in docs)

    r = await upload(ctx, h, "mine.txt", b"my own text")
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    assert (await ctx.delete(f"/documents/{docs[0]['id']}", headers=h)).status_code == 403


async def test_demo_can_chat_and_chats_are_not_stored(ctx):
    h = await _demo_headers(ctx)
    r = await ctx.post("/chat", json={"question": "How many days do I have for a refund?"}, headers=h)
    assert r.status_code == 200 and "event: sources" in r.text
    put = await ctx.put("/threads/demo-thread-1", json={"title": "x", "messages": [{"role": "user", "content": "hi"}]}, headers=h)
    assert put.status_code == 200
    assert (await ctx.get("/threads", headers=h)).json() == []


async def test_demo_seeding_is_idempotent_and_isolated(ctx):
    h = await _demo_headers(ctx)
    await _demo_headers(ctx)  # second click must not duplicate documents
    assert len((await ctx.get("/documents", headers=h)).json()) == 4
    other = await ctx.post("/auth/register", json={"email": "z@example.com", "password": "password123"})
    h2 = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert (await ctx.get("/documents", headers=h2)).json() == []


async def test_demo_can_be_disabled(ctx, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "demo_enabled", False)
    assert (await ctx.post("/auth/demo")).status_code == 404
