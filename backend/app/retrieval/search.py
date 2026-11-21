import logging
import time
from dataclasses import dataclass

from app.core import metrics
from app.core.config import Settings
from app.core.tracing import fail, tracer
from app.embeddings.embedder import Embedder
from app.retrieval.rerank import Reranker
from app.retrieval.store import Hit, VectorStore

logger = logging.getLogger(__name__)


@dataclass
class Retrieval:
    hits: list[Hit]
    retrieval_ms: float  # embed + search + rerank
    rerank_ms: float = 0.0
    embed_ms: float = 0.0
    search_ms: float = 0.0


async def retrieve(
    embedder: Embedder,
    store: VectorStore,
    reranker: Reranker | None,
    user_id: str,
    query: str,
    settings: Settings,
    otel_ctx=None,
) -> Retrieval:
    """embed -> (hybrid | dense) search, user-filtered -> rerank -> threshold -> top_n.

    Threshold meaning: with a reranker it is `rerank_threshold` on the reranker's 0..1 score;
    without one it is `relevance_threshold` on the dense cosine."""
    t0 = time.perf_counter()
    # otel_ctx lets the caller (an async generator) pin the parent span explicitly.
    with tracer.start_as_current_span("retrieval", context=otel_ctx) as span:
        with tracer.start_as_current_span("embed_query"):
            vector = (await embedder.embed([query]))[0]
        embed_ms = (time.perf_counter() - t0) * 1000
        t_search = time.perf_counter()
        with tracer.start_as_current_span("vector_search") as s:
            s.set_attribute("search.hybrid", settings.hybrid_enabled)
            if settings.hybrid_enabled:
                candidates = await store.hybrid_search(user_id, vector, query, settings.retrieve_top_k)
            else:
                candidates = await store.search(user_id, vector, settings.retrieve_top_k)
            s.set_attribute("search.candidates", len(candidates))
        search_ms = (time.perf_counter() - t_search) * 1000

        threshold = settings.relevance_threshold
        rerank_ms = 0.0
        if reranker is not None and candidates:
            candidates = candidates[: settings.rerank_candidates]  # fused order: best first
            t1 = time.perf_counter()
            with tracer.start_as_current_span("rerank") as s:
                try:
                    scores = await reranker.score(query, [h.text for h in candidates])
                    for hit, sc in zip(candidates, scores):
                        hit.rerank_score = sc
                        hit.score = sc
                    threshold = settings.rerank_threshold
                except Exception as exc:
                    # Degrade gracefully: keep dense scores and the dense threshold.
                    logger.exception("Reranker failed; falling back to dense scores")
                    metrics.RERANKER_FALLBACKS.inc()
                    fail(s, exc)
            rerank_ms = (time.perf_counter() - t1) * 1000

        retrieval_ms = (time.perf_counter() - t0) * 1000
        hits = sorted(candidates, key=lambda h: h.score, reverse=True)
        hits = [h for h in hits if h.score >= threshold][: settings.context_top_n]
        span.set_attribute("retrieval.hits", len(hits))
        if hits:
            span.set_attribute("retrieval.top_score", round(hits[0].score, 4))
    return Retrieval(hits=hits, retrieval_ms=retrieval_ms, rerank_ms=rerank_ms,
                     embed_ms=embed_ms, search_ms=search_ms)