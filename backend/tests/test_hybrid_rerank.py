import numpy as np
import pytest

from app.chunking.splitter import Chunk
from app.core.config import get_settings
from app.main import app
from app.retrieval.fusion import rrf
from app.retrieval.search import retrieve
from app.retrieval.sparse import document_vector, query_vector, tokenize
from app.retrieval.store import Hit
from tests.fakes import FakeEmbedder, FakeReranker

settings = get_settings()
DIM = FakeEmbedder.dim


def _unit(seed: int) -> list[float]:
    v = np.random.default_rng(seed).normal(size=DIM)
    return (v / np.linalg.norm(v)).tolist()


# ---------- sparse (BM25) vectors ----------
def test_tokenize_lowercases_drops_stopwords_keeps_hindi_and_numbers():
    assert tokenize("The Invoice #4521 is due") == ["invoice", "4521", "due"]
    # Hindi words must stay whole (a plain \w+ regex would shred them at the vowel signs)
    assert tokenize("रिफंड नीति what is the") == ["रिफंड", "नीति"]


async def test_hindi_keyword_search_finds_the_right_chunk(ctx):
    from app.main import app as _app

    texts = ["शिपिंग में तीन दिन लगते हैं", "रिफंड नीति के अनुसार चौदह दिन में पैसे वापस मिलते हैं", "contact support"]
    await _seed(_app.state.store, "u1", texts, vectors=[_unit(i) for i in range(3)])  # dense = noise
    hits = await _app.state.store.hybrid_search("u1", _unit(50), "रिफंड नीति", top_k=2)
    assert "रिफंड" in hits[0].text


def test_sparse_ids_are_stable_and_query_terms_deduped():
    assert document_vector("invoice payment").indices == document_vector("invoice payment").indices
    q = query_vector("invoice invoice INVOICE payment")
    assert len(q.indices) == 2 and q.values == [1.0, 1.0]


def test_bm25_tf_saturates_and_long_docs_are_penalised():
    one = dict(zip(*[(d := document_vector("invoice")).indices, d.values]))
    ten = dict(zip(*[(d := document_vector("invoice " * 10)).indices, d.values]))
    (w1,), (w10,) = one.values(), ten.values()
    assert w10 < 10 * w1  # saturation, not linear
    filler = " ".join(f"filler{i}" for i in range(600))
    long_doc = document_vector("invoice " + filler)
    short = document_vector("invoice other")
    assert dict(zip(short.indices, short.values))[query_vector("invoice").indices[0]] > dict(
        zip(long_doc.indices, long_doc.values)
    )[query_vector("invoice").indices[0]]


def test_rrf_rewards_agreement_between_rankings():
    scores = rrf([["a", "b", "c"], ["d", "b", "e"]])
    ordered = sorted(scores, key=scores.get, reverse=True)
    assert ordered[0] == "b"  # rank 2 in both lists beats rank 1 in only one
    assert set(ordered) == {"a", "b", "c", "d", "e"}


# ---------- store: hybrid search ----------
async def _seed(store, user_id, texts, vectors=None):
    emb = FakeEmbedder()
    vectors = vectors or await emb.embed(texts)
    chunks = [Chunk(i, 1, t) for i, t in enumerate(texts)]
    await store.upsert_chunks(user_id=user_id, doc_id="d1", filename="f.txt", chunks=chunks, vectors=vectors)


async def test_keyword_leg_rescues_exact_match_when_dense_is_noise(ctx):
    store = app.state.store
    texts = [f"generic paragraph number {i} about nothing in particular" for i in range(8)]
    texts[5] = "Invoice 4521 payment is due on the first of March"
    await _seed(store, "u1", texts, vectors=[_unit(i) for i in range(8)])  # dense carries no signal

    hits = await store.hybrid_search("u1", _unit(99), "invoice 4521", top_k=3)
    assert any("4521" in h.text for h in hits)


async def test_bm25_idf_makes_rare_terms_outrank_common_ones(ctx):
    from app.retrieval.store import SPARSE

    store = app.state.store
    texts = ["policy policy policy policy", "policy about shipping", "policy about returns", "zebra crossing"]
    await _seed(store, "u1", texts, vectors=[_unit(i) for i in range(4)])
    res = await store.client.query_points(
        store.collection, query=query_vector("policy zebra"), using=SPARSE, limit=4, with_payload=True
    )
    assert "zebra" in res.points[0].payload["text"]  # rare term wins over the common repeated one


async def test_hybrid_never_leaks_other_users_even_on_exact_keyword(ctx):
    store = app.state.store
    await _seed(store, "alice", ["Secret invoice 4521 payment"])
    assert await store.hybrid_search("bob", _unit(1), "invoice 4521", top_k=8) == []
    assert len(await store.hybrid_search("alice", _unit(1), "invoice 4521", top_k=8)) == 1


async def test_hits_carry_real_dense_cosine(ctx):
    store, emb = app.state.store, FakeEmbedder()
    await _seed(store, "u1", ["refund policy details"])
    vec = (await emb.embed(["refund policy details"]))[0]
    (hit,) = await store.hybrid_search("u1", vec, "refund policy", top_k=8)
    assert hit.dense_score == pytest.approx(1.0, abs=1e-4) and hit.score == hit.dense_score


async def test_stopword_only_query_falls_back_to_dense_leg(ctx):
    store, emb = app.state.store, FakeEmbedder()
    await _seed(store, "u1", ["refund policy details"])
    vec = (await emb.embed(["refund policy details"]))[0]
    assert len(await store.hybrid_search("u1", vec, "what is the", top_k=8)) == 1


# ---------- retrieve(): rerank / threshold / fallback ----------
class StubStore:
    def __init__(self, hits):
        self.hits = hits

    async def hybrid_search(self, user_id, vector, query, top_k):
        return [Hit(**h.__dict__) for h in self.hits]

    async def search(self, user_id, vector, top_k):
        return await self.hybrid_search(user_id, vector, "", top_k)


def _hit(text, dense):
    return Hit(score=dense, dense_score=dense, text=text, filename="f.txt", page=1, doc_id="d", chunk_index=0)


async def test_reranker_reorders_candidates_and_applies_rerank_threshold():
    # dense likes the wrong chunk best; the reranker knows better
    store = StubStore([_hit("totally unrelated text", 0.9), _hit("refund policy covers purchases", 0.4)])
    r = await retrieve(FakeEmbedder(), store, FakeReranker(), "u", "refund policy", settings)
    assert [h.text for h in r.hits] == ["refund policy covers purchases"]  # unrelated one < 0.2
    assert r.hits[0].rerank_score == 1.0 and r.rerank_ms >= 0


async def test_without_reranker_uses_dense_score_and_dense_threshold():
    store = StubStore([_hit("a", 0.9), _hit("b", 0.05)])
    r = await retrieve(FakeEmbedder(), store, None, "u", "anything", settings)
    assert [h.text for h in r.hits] == ["a"]  # 0.05 < relevance_threshold (0.1 in tests)
    assert r.rerank_ms == 0


async def test_reranker_failure_degrades_to_dense_instead_of_erroring():
    reranker = FakeReranker()
    reranker.fail = True
    store = StubStore([_hit("good", 0.9), _hit("weak", 0.01)])
    r = await retrieve(FakeEmbedder(), store, reranker, "u", "q", settings)
    assert [h.text for h in r.hits] == ["good"]


async def test_context_is_capped_at_top_n():
    store = StubStore([_hit(f"refund policy {i}", 0.5) for i in range(8)])
    r = await retrieve(FakeEmbedder(), store, FakeReranker(), "u", "refund policy", settings)
    assert len(r.hits) == settings.context_top_n


async def test_dense_only_mode(ctx, monkeypatch):
    monkeypatch.setattr(settings, "hybrid_enabled", False)
    store = app.state.store
    await _seed(store, "u1", ["refund policy details"])
    r = await retrieve(FakeEmbedder(), store, FakeReranker(), "u1", "refund policy", settings)
    assert len(r.hits) == 1


# ---------------- Qdrant client construction ----------------
def test_make_client_memory_and_api_key():
    from app.retrieval.store import make_client

    assert make_client(":memory:") is not None
    # constructing a client for a server must not connect, and must accept an API key (Qdrant Cloud)
    c = make_client("https://example.qdrant.io:6333", "secret-key")
    assert c is not None
