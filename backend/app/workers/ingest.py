"""parse -> chunk -> embed -> upsert. No FastAPI/DB dependencies, so it is reusable anywhere."""
import asyncio
from collections.abc import Callable

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
) -> int:
    with tracer.start_as_current_span("parse") as span:
        span.set_attribute("file.type", ftype)
        pages = await asyncio.to_thread(
            parse_file, path, ftype, ocr, settings.ocr_min_chars, settings.ocr_max_pages
        )
        span.set_attribute("file.pages", len(pages))
    chunks = chunk_pages(pages, settings.chunk_size_chars, settings.chunk_overlap_chars)
    if not chunks:
        raise ParseError("File contains no text")
    with tracer.start_as_current_span("embed") as span:
        span.set_attribute("chunks", len(chunks))
        vectors = await embedder.embed([c.text for c in chunks])
    with tracer.start_as_current_span("upsert"):
        await store.upsert_chunks(
            user_id=user_id, doc_id=doc_id, filename=filename, chunks=chunks, vectors=vectors
        )
    return len(chunks)
