"""Document lifecycle: uploaded -> processing -> ready | failed.

Used by the Celery task (production) and called inline in dev/tests.
"""
import asyncio
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable

from app.core import metrics
from app.core.config import Settings
from app.core.storage import Storage
from app.core.tracing import fail as fail_span
from app.core.tracing import tracer
from app.embeddings.embedder import Embedder
from app.models import DocStatus, Document
from app.ocr.tesseract import make_ocr
from app.parsers.files import ParseError
from app.retrieval.store import VectorStore
from app.workers.ingest import ingest_document

logger = logging.getLogger(__name__)


async def _cleanup(storage: Storage, store: VectorStore, user_id: str, doc_id: str, key: str) -> None:
    try:
        await store.delete_document(user_id, doc_id)
    except Exception:
        logger.exception("Vector cleanup failed for doc %s", doc_id)
    try:
        await asyncio.to_thread(storage.delete, key)
    except Exception:
        logger.exception("File cleanup failed for doc %s", doc_id)


async def process_document(
    doc_id: str,
    *,
    session_factory,
    storage: Storage,
    embedder: Embedder,
    store: VectorStore,
    settings: Settings,
    ocr: Callable[[int], str] | None = None,
    final_attempt: bool = True,
    cache=None,
) -> None:
    """Idempotent: safe to run twice for the same doc (ready docs are skipped, vector ids are
    deterministic). Non-parse errors are re-raised unless `final_attempt`, so the queue can retry
    transient failures (Qdrant/S3 hiccups) before the doc is marked failed."""
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        if doc is None or doc.status == DocStatus.READY:
            return  # deleted before the worker got to it, or already done
        user_id, filename, key, ftype = doc.user_id, doc.filename, doc.storage_path, doc.file_type
        doc.status = DocStatus.PROCESSING
        doc.error = None
        await session.commit()

        started = time.perf_counter()

        async def fail(message: str) -> None:
            current = await session.get(Document, doc_id, populate_existing=True)
            if current is not None:
                current.status = DocStatus.FAILED
                current.error = message
                current.num_chunks = 0
                await session.commit()
            await _cleanup(storage, store, user_id, doc_id, key)
            metrics.DOCS_INGESTED.labels("failed").inc()
            metrics.INGEST_DURATION.observe(time.perf_counter() - started)
            logger.warning("document failed", extra={"doc_id": doc_id, "reason": message})

        tmpdir = tempfile.mkdtemp(prefix="ingest-")
        with tracer.start_as_current_span(
            "ingest_document", attributes={"doc.id": doc_id, "user.id": user_id, "file.type": ftype}
        ) as span:
            try:
                path = os.path.join(tmpdir, f"doc.{ftype}")
                await asyncio.to_thread(storage.download, key, path)
                page_ocr = ocr
                if page_ocr is None and settings.ocr_enabled and ftype == "pdf":
                    page_ocr = make_ocr(path, settings)

                num_chunks = await ingest_document(
                    user_id=user_id, doc_id=doc_id, filename=filename, path=path, ftype=ftype,
                    embedder=embedder, store=store, settings=settings, ocr=page_ocr,
                )

                current = await session.get(Document, doc_id, populate_existing=True)
                if current is None:
                    # user deleted the doc while we were working: remove what we just wrote
                    await _cleanup(storage, store, user_id, doc_id, key)
                    return
                current.status = DocStatus.READY
                current.num_chunks = num_chunks
                await session.commit()
                span.set_attribute("doc.chunks", num_chunks)
                metrics.DOCS_INGESTED.labels("ready").inc()
                metrics.INGEST_DURATION.observe(time.perf_counter() - started)
                logger.info(
                    "document ready",
                    extra={"doc_id": doc_id, "chunks": num_chunks,
                           "duration_s": round(time.perf_counter() - started, 2)},
                )
                if cache is not None:
                    await cache.invalidate_user(user_id)  # new content: old cached answers are stale
            except ParseError as e:
                fail_span(span, e)
                await fail(str(e))
            except Exception as e:
                logger.exception("Ingestion error for doc %s (final_attempt=%s)", doc_id, final_attempt)
                fail_span(span, e)
                if not final_attempt:
                    raise
                await fail("Ingestion failed. Please try again.")
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
