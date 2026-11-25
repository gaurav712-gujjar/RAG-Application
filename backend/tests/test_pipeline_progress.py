"""Document pipeline stages shown in the UI, and the rerank candidate cap."""
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.main import app
from app.models import Document
from app.retrieval.search import retrieve
from app.retrieval.store import Hit
from tests.fakes import FakeEmbedder, FakeReranker
from tests.helpers import signup, upload

settings = get_settings()
TEXT = (b"Refunds are accepted within 14 days of purchase. " * 80)


async def _stage_of_only_doc() -> str | None:
    async with SessionLocal() as s:
        return (await s.execute(select(Document))).scalars().one().stage


async def test_finished_document_reports_stage_done_and_per_stage_timings(ctx):
    h = await signup(ctx, "p1@example.com")
    assert (await upload(ctx, h, "a.txt", TEXT)).status_code in (200, 201)
    (doc,) = (await ctx.get("/documents", headers=h)).json()
    assert doc["status"] == "ready" and doc["stage"] == "done"
    t = doc["timings"]
    assert {"parse_ms", "split_ms", "embed_ms", "index_ms", "pages", "chunks"} <= set(t)
    assert t["chunks"] == doc["num_chunks"] > 0 and t["pages"] >= 1
    assert all(t[k] >= 0 for k in ("parse_ms", "split_ms", "embed_ms", "index_ms"))


async def test_stage_is_recorded_while_each_step_runs(ctx):
    seen: dict[str, str | None] = {}

    class SpyEmbedder(FakeEmbedder):
        async def embed(self, texts):
            seen["during_embed"] = await _stage_of_only_doc()
            return await super().embed(texts)

    app.state.embedder = SpyEmbedder()
    h = await signup(ctx, "p2@example.com")
    await upload(ctx, h, "a.txt", TEXT)
    assert seen["during_embed"] == "embedding"


async def test_failed_document_keeps_the_stage_where_it_broke(ctx):
    class BrokenEmbedder(FakeEmbedder):
        async def embed(self, texts):
            raise RuntimeError("model crashed")

    app.state.embedder = BrokenEmbedder()
    h = await signup(ctx, "p3@example.com")
    await upload(ctx, h, "a.txt", TEXT)
    (doc,) = (await ctx.get("/documents", headers=h)).json()
    assert doc["status"] == "failed" and doc["stage"] == "embedding"
    assert doc["timings"] and "parse_ms" in doc["timings"]  # finished stages stay visible


async def test_new_upload_starts_in_queued_stage(ctx, monkeypatch):
    monkeypatch.setattr(settings, "ingest_mode", "celery")
    monkeypatch.setattr("app.api.documents.enqueue_ingestion", lambda doc_id: None)
    h = await signup(ctx, "p4@example.com")
    r = await upload(ctx, h, "a.txt", TEXT)
    assert r.status_code == 202 and r.json()["stage"] == "queued"


class _Store:
    def __init__(self, n):
        self.hits = [
            Hit(score=0.5, dense_score=0.5, text=f"refund policy {i}", filename="f", page=1, doc_id="d", chunk_index=i)
            for i in range(n)
        ]

    async def hybrid_search(self, user_id, vector, query, top_k):
        return self.hits


async def test_only_the_best_candidates_are_sent_to_the_reranker(monkeypatch):
    monkeypatch.setattr(settings, "rerank_candidates", 4)
    seen = []

    class Spy(FakeReranker):
        async def score(self, query, texts):
            seen.append(len(texts))
            return await super().score(query, texts)

    r = await retrieve(FakeEmbedder(), _Store(8), Spy(), "u", "refund policy", settings)
    assert seen == [4]
    assert r.embed_ms >= 0 and r.search_ms >= 0
