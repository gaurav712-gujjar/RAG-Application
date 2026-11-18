import pytest
from qdrant_client import models

from app.chunking.splitter import Chunk
from app.llm.prompts import NOT_FOUND
from app.main import app
from app.retrieval.store import user_filter
from tests.fakes import FakeEmbedder
from tests.helpers import parse_sse, signup, upload

DOC = b"Refunds are processed within 14 days. The refund policy covers all purchases."


def test_user_filter_always_has_user_id_condition():
    f = user_filter("u1")
    assert any(c.key == "user_id" and c.match.value == "u1" for c in f.must)
    f2 = user_filter("u1", "d9")
    assert {c.key for c in f2.must} == {"user_id", "doc_id"}


def test_user_filter_rejects_empty_user():
    with pytest.raises(ValueError):
        user_filter("")


async def test_store_never_returns_other_users_vectors(ctx):
    store, emb = app.state.store, FakeEmbedder()
    chunk = Chunk(0, 1, "refund policy secret")
    vec = (await emb.embed([chunk.text]))[0]
    await store.upsert_chunks(user_id="alice", doc_id="d1", filename="a.txt", chunks=[chunk], vectors=[vec])

    assert len(await store.search("alice", vec, 8)) == 1
    assert await store.search("bob", vec, 8) == []

    # bob cannot delete alice's vectors either
    await store.delete_document("bob", "d1")
    assert len(await store.search("alice", vec, 8)) == 1


async def test_chat_cannot_see_other_users_documents(ctx):
    alice = await signup(ctx, "alice@example.com")
    bob = await signup(ctx, "bob@example.com")
    r = await upload(ctx, alice, "policy.txt", DOC)
    assert r.status_code == 201 and r.json()["status"] == "ready"

    # Alice finds it
    ra = await ctx.post("/chat", headers=alice, json={"question": "What is the refund policy?"})
    assert any(e == "sources" and d for e, d in parse_sse(ra.text))

    # Bob does not, and no LLM call is made for him
    calls_before = app.state.llm.calls
    rb = await ctx.post("/chat", headers=bob, json={"question": "What is the refund policy?"})
    events = dict(parse_sse(rb.text))
    assert events["sources"] == []
    assert events["token"]["t"] == NOT_FOUND
    assert app.state.llm.calls == calls_before


async def test_client_supplied_user_id_is_ignored(ctx):
    alice = await signup(ctx, "alice@example.com")
    bob = await signup(ctx, "bob@example.com")
    await upload(ctx, alice, "policy.txt", DOC)
    rb = await ctx.post(
        "/chat", headers=bob, json={"question": "What is the refund policy?", "user_id": "whatever"}
    )
    assert dict(parse_sse(rb.text))["sources"] == []


async def test_cannot_delete_other_users_document(ctx):
    alice = await signup(ctx, "alice@example.com")
    bob = await signup(ctx, "bob@example.com")
    doc_id = (await upload(ctx, alice, "policy.txt", DOC)).json()["id"]
    assert (await ctx.delete(f"/documents/{doc_id}", headers=bob)).status_code == 404
    assert len((await ctx.get("/documents", headers=alice)).json()) == 1
