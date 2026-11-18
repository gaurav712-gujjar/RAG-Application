"""Prometheus metrics.

Run ONE uvicorn process per container and scale with replicas: prometheus_client keeps counters in
process memory, so multiple workers per container would each report a different slice.
Labels are kept low-cardinality on purpose (no user ids, no raw paths, no question text).
"""
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# Time-to-first-token is the headline number: fine-grained buckets between 50 ms and 10 s,
# so p50/p95 from histogram_quantile() are accurate where it matters.
_LATENCY_BUCKETS = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0, 30.0)

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request duration (for /chat: until the stream ends)",
    ["method", "route"], buckets=_LATENCY_BUCKETS,
)
HTTP_IN_FLIGHT = Gauge("http_requests_in_flight", "Requests currently being handled")

CHAT_REQUESTS = Counter(
    "chat_requests_total",
    "Chat requests by outcome: ok, cached, not_found, error, llm_unavailable, quota_exceeded, "
    "aborted (client disconnected mid-answer)",
    ["outcome"],
)
# source=llm is the real model TTFT. cache / not_found answers never wait for the model, so they
# get their own series instead of dragging the llm p95 down.
CHAT_TTFT = Histogram(
    "chat_ttft_seconds", "Time to first token, request start to first token", ["source"], buckets=_LATENCY_BUCKETS
)
CHAT_RETRIEVAL = Histogram("chat_retrieval_seconds", "Embed + search + rerank", buckets=_LATENCY_BUCKETS)
CHAT_RERANK = Histogram("chat_rerank_seconds", "Reranker only", buckets=_LATENCY_BUCKETS)
CHAT_TOTAL = Histogram("chat_total_seconds", "Whole chat request until the last token", buckets=_LATENCY_BUCKETS)

LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["model", "kind"])  # kind: prompt|completion|rewrite
CACHE_LOOKUPS = Counter("answer_cache_lookups_total", "Answer cache lookups", ["result"])  # hit|miss
RERANKER_FALLBACKS = Counter("reranker_fallbacks_total", "Reranker errors that fell back to dense scores")
REDIS_UNAVAILABLE = Counter(
    "redis_unavailable_total", "Redis errors that were skipped (cache/quota/rate limit fail open)", ["feature"]
)

DOCS_INGESTED = Counter("documents_ingested_total", "Documents processed by the worker", ["status"])  # ready|failed
INGEST_DURATION = Histogram(
    "ingest_duration_seconds", "Worker time per document", buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300, 600)
)
OCR_PAGES = Counter("ocr_pages_total", "PDF pages sent through OCR")


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
