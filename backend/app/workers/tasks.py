import asyncio
import logging

import redis.asyncio as aioredis
from celery.signals import worker_process_init
from prometheus_client import start_http_server
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.cache import AnswerCache
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.storage import get_storage
from app.core.tracing import setup_tracing
from app.embeddings.embedder import SentenceTransformerEmbedder
from app.retrieval.store import VectorStore, make_client
from app.workers.celery_app import celery_app
from app.workers.pipeline import process_document

settings = get_settings()
setup_logging(settings.log_level, settings.log_format)
logger = logging.getLogger(__name__)

_embedder: SentenceTransformerEmbedder | None = None


@worker_process_init.connect
def _init_worker_process(**_):
    """Runs in each forked worker process (exporter threads and sockets must be created after the
    fork, not before). Starts the tracing exporter and a /metrics HTTP server on
    WORKER_METRICS_PORT. Run one process per pod (`--concurrency=1`): with more, only the first
    process can bind the port, so scale with replicas instead."""
    setup_tracing("docchat-worker")
    if settings.metrics_enabled:
        try:
            start_http_server(settings.worker_metrics_port)
            logger.info("worker metrics on :%d", settings.worker_metrics_port)
        except OSError:
            logger.warning("worker metrics port %d busy; this process exports no metrics", settings.worker_metrics_port)


def _get_embedder() -> SentenceTransformerEmbedder:
    """Model is loaded once per worker process and reused across tasks."""
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_dim)
    return _embedder


async def _run(doc_id: str, final_attempt: bool) -> None:
    # asyncio.run() creates a new event loop per task, so DB/Qdrant clients are built per task
    # (a pooled engine from another loop would break).
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    client = make_client(settings.qdrant_url, settings.qdrant_api_key)
    redis_client = (
        aioredis.from_url(settings.redis_url, decode_responses=True)
        if settings.redis_url and settings.cache_enabled
        else None
    )
    cache = AnswerCache(redis_client, settings.cache_ttl_s) if redis_client is not None else None
    try:
        storage = get_storage()
        await asyncio.to_thread(storage.ensure_ready)
        store = VectorStore(client, settings.qdrant_collection, settings.embedding_dim)
        await store.ensure_collection()
        await process_document(
            doc_id,
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            storage=storage,
            embedder=_get_embedder(),
            store=store,
            settings=settings,
            final_attempt=final_attempt,
            cache=cache,  # new/changed documents invalidate that user's cached answers
        )
    finally:
        if redis_client is not None:
            await redis_client.aclose()
        await client.close()
        await engine.dispose()


@celery_app.task(bind=True, name="ingest_document", max_retries=2)
def ingest_task(self, doc_id: str) -> None:
    final_attempt = self.request.retries >= self.max_retries
    try:
        asyncio.run(_run(doc_id, final_attempt))
    except Exception as exc:  # only reached for non-final attempts
        raise self.retry(exc=exc, countdown=15 * (2**self.request.retries))
