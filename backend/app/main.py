import asyncio
import hmac
import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, documents, usage
from app.auth import router as auth_router
from app.core.cache import AnswerCache
from app.core.config import get_settings
from app.core import metrics
from app.core.db import init_db
from app.core.limits import RateLimiter, TokenQuota
from app.core.logging import setup_logging
from app.core.middleware import ObservabilityMiddleware
from app.core.storage import get_storage
from app.core.tracing import setup_tracing, shutdown_tracing
from app.embeddings.embedder import SentenceTransformerEmbedder
from app.llm.groq_client import GroqLLM
from app.retrieval.rerank import CrossEncoderReranker
from app.retrieval.store import VectorStore, make_client

settings = get_settings()
setup_logging(settings.log_level, settings.log_format)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing(settings.otel_service_name)  # no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set
    await init_db()

    # Components are only built if not already injected (tests inject fakes).
    if not hasattr(app.state, "storage"):
        storage = get_storage()
        await asyncio.to_thread(storage.ensure_ready)  # creates the bucket / folder
        app.state.storage = storage

    if not hasattr(app.state, "embedder"):
        embedder = SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_dim)
        logger.info("Loading embedding model %s ...", settings.embedding_model)
        await embedder.warmup()
        app.state.embedder = embedder

    if not hasattr(app.state, "reranker"):
        reranker = None
        if settings.rerank_enabled:
            reranker = CrossEncoderReranker(settings.reranker_model, settings.rerank_max_length)
            logger.info("Loading reranker %s ...", settings.reranker_model)
            await reranker.warmup()
        app.state.reranker = reranker

    if not hasattr(app.state, "store"):
        client = make_client(settings.qdrant_url, settings.qdrant_api_key)
        store = VectorStore(client, settings.qdrant_collection, settings.embedding_dim)
        await store.ensure_collection()
        app.state.store = store

    # Redis powers the answer cache, token quota and rate limits. All three fail open.
    created_redis = None
    if not hasattr(app.state, "redis"):
        created_redis = aioredis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None
        app.state.redis = created_redis
    r = app.state.redis
    if not hasattr(app.state, "cache"):
        app.state.cache = AnswerCache(r, settings.cache_ttl_s) if r is not None and settings.cache_enabled else None
    if not hasattr(app.state, "quota"):
        app.state.quota = TokenQuota(r, settings.daily_token_quota) if r is not None else None
    if not hasattr(app.state, "limiter"):
        app.state.limiter = RateLimiter(r) if r is not None else None

    if not hasattr(app.state, "llm"):
        if not settings.groq_api_key:
            logger.warning("GROQ_API_KEY is not set: /chat will return an error until it is.")
        app.state.llm = GroqLLM(
            settings.groq_api_key,
            max_retries=settings.llm_max_retries,
            timeout_s=settings.llm_timeout_s,
        )
    yield
    if created_redis is not None:
        await created_redis.aclose()
    shutdown_tracing()  # flush buffered spans before the process exits


app = FastAPI(title="DocChat RAG", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-Request-ID"],
)
# Added last = outermost: request IDs, access log and metrics cover everything, including CORS and 429s.
app.add_middleware(ObservabilityMiddleware, metrics_enabled=settings.metrics_enabled)
app.include_router(auth_router.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(usage.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request):
    """Prometheus scrape endpoint. Do not expose it through the public ingress; set METRICS_TOKEN
    to also require a bearer token."""
    if not settings.metrics_enabled:
        raise HTTPException(404)
    if settings.metrics_token and not hmac.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {settings.metrics_token}"
    ):
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
    body, content_type = metrics.render()
    return Response(body, media_type=content_type)