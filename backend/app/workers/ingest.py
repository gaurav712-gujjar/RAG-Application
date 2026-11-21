"""parse -> chunk -> embed -> upsert. No FastAPI/DB dependencies, so it is reusable anywhere."""
import asyncio
import time
from collections.abc import Awaitable, Callable

from app.chunking.splitter import chunk_pages
from app.core.config import Settings
from app.core.tracing import tracer
from app.embeddings.embedder import Embedder
from app.parsers.files import ParseError, parse_file
from app.retrieval.store import VectorStore


async def ingest_document(
    *,
    user_id: str,
    doc_id: str,
    filename: str,
    path: str,
    ftype: str,
    embedder: Embedder,
    store: VectorStore,
    settings: Settings,
    ocr: Callable[[int], str] | None = None,
    progress: Callable[[str, dict], Awaitable[None]] | None = None,
) -> int:
    """`progress(stage, timings)` is awaited when a stage starts (timings = finished stages so far)."""
    timings: dict = {}

    async def enter(stage: str) -> float:
        if progress is not None:
            await progress(stage, dict(timings))
        return time.perf_counter()

    def took(t: float) -> float:
        return round((time.perf_counter() - t) * 1000, 1)

    t = await enter("parsing")
    with tracer.start_as_current_span("parse") as span:
        span.set_attribute("file.type", ftype)
        pages = await asyncio.to_thread(
            parse_file, path, ftype, ocr, settings.ocr_min_chars, settings.ocr_max_pages
        )
        span.set_attribute("file.pages", len(pages))
    timings["parse_ms"], timings["pages"] = took(t), len(pages)
    t = await enter("splitting")
    chunks = chunk_pages(pages, settings.chunk_size_chars, settings.chunk_overlap_chars)
    if not chunks:
        raise ParseError("File contains no text")
    timings["split_ms"], timings["chunks"] = took(t), len(chunks)
    t = await enter("embedding")
    with tracer.start_as_current_span("embed") as span:
        span.set_attribute("chunks", len(chunks))
        vectors = await embedder.embed([c.text for c in chunks])
    timings["embed_ms"] = took(t)
    t = await enter("indexing")
    with tracer.start_as_current_span("upsert"):
        await store.upsert_chunks(
            user_id=user_id, doc_id=doc_id, filename=filename, chunks=chunks, vectors=vectors
        )
    timings["index_ms"] = took(t)
    if progress is not None:
        await progress("done", dict(timings))
    return len(chunks)