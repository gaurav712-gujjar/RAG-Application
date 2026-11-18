"""Run the real retrieval (and optionally generation) code over the golden documents.

Components are injected, so the same code runs with the real models (nightly / before releases)
or with fast fakes (CI plumbing checks and unit tests)."""
import asyncio
import re
import time
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings, get_settings
from app.llm.prompts import NOT_FOUND, build_messages, choose_model
from app.retrieval.search import retrieve
from app.retrieval.store import VectorStore
from app.workers.ingest import ingest_document
from evals.dataset import QA, doc_paths
from evals.metrics import GenerationResult, RetrievalResult

EVAL_USER = "eval-user"
_CITATION = re.compile(r"\[\d+\]")


def eval_settings(**overrides) -> Settings:
    """Small chunks so each golden doc becomes several chunks and retrieval actually has to choose."""
    base = {"chunk_size_chars": 300, "chunk_overlap_chars": 30}
    base.update(overrides)
    return get_settings().model_copy(update=base)


@dataclass
class Index:
    store: VectorStore
    embedder: object
    reranker: object | None
    settings: Settings
    n_chunks: int


async def build_index(embedder, reranker, settings: Settings) -> Index:
    client = AsyncQdrantClient(location=":memory:")
    store = VectorStore(client, "eval", embedder.dim)
    await store.ensure_collection()
    total = 0
    for path in doc_paths():
        total += await ingest_document(
            user_id=EVAL_USER, doc_id=path.stem, filename=path.name, path=str(path), ftype="md",
            embedder=embedder, store=store, settings=settings,
        )
    return Index(store, embedder, reranker, settings, total)


async def run_retrieval(index: Index, qa: list[QA]) -> tuple[list[RetrievalResult], dict[str, list]]:
    results, hits_by_id = [], {}
    for q in qa:
        r = await retrieve(index.embedder, index.store, index.reranker, EVAL_USER, q.question, index.settings)
        hits_by_id[q.id] = r.hits
        results.append(RetrievalResult(
            qa_id=q.id, tag=q.tag, answerable=q.answerable, source=q.source, evidence=q.evidence,
            retrieved_sources=[h.filename for h in r.hits], retrieved_texts=[h.text for h in r.hits],
        ))
    return results, hits_by_id


async def run_generation(index: Index, llm, qa: list[QA], hits_by_id: dict[str, list], delay_s: float = 0.0):
    """Same prompt, model routing and abstain-without-LLM behaviour as the live /chat endpoint."""
    s = index.settings
    results, rows = [], []
    for q in qa:
        hits = hits_by_id[q.id]
        answer, tokens, ttft = NOT_FOUND, 0, None
        if hits:  # no hits -> the app skips the LLM and answers NOT_FOUND
            if delay_s:
                await asyncio.sleep(delay_s)  # be gentle with provider rate limits
            model = choose_model(q.question, s)
            t0 = time.perf_counter()
            parts = []
            async for ev in llm.stream(build_messages(q.question, hits, []), model, s.max_output_tokens, s.temperature):
                if ev.kind == "token" and ev.text:
                    if ttft is None:
                        ttft = round((time.perf_counter() - t0) * 1000, 1)
                    parts.append(ev.text)
                elif ev.kind == "usage":
                    tokens = (ev.prompt_tokens or 0) + (ev.completion_tokens or 0)
            answer = "".join(parts).strip()
        results.append(GenerationResult(
            qa_id=q.id, tag=q.tag, answerable=q.answerable, answer=answer,
            abstained=NOT_FOUND.rstrip(".").lower() in answer.lower(), key_fact=q.key_fact,
            cited=bool(_CITATION.search(answer)), tokens=tokens, ttft_ms=ttft,
        ))
        rows.append({"question": q.question, "answer": answer, "contexts": [h.text for h in hits],
                     "ground_truth": q.ground_truth, "answerable": q.answerable})
    return results, rows
