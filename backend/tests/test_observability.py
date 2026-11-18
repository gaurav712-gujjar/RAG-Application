import json
import logging

import pytest
from starlette.requests import Request

from app.api.chat import ChatRequest, chat
from app.chunking.splitter import Chunk
from app.core.config import get_settings
from app.core.logging import JsonFormatter, new_request_id, request_id_var, setup_logging
from app.core.storage import LocalStorage
from app.main import app
from app.models import User
from app.ocr import tesseract
from app.workers.pipeline import process_document
from app.core.db import SessionLocal
from tests.fakes import FakeEmbedder, FakeLLM
from tests.helpers import parse_sse, signup, upload
from tests.telemetry import SPANS, app_spans, sample, spans_by_name

settings = get_settings()
DOC = b"Refunds are processed within 14 days. The refund policy covers all purchases."
Q = "What is the refund policy?"
FAST = settings.llm_model_fast


async def _user_with_doc(ctx, email="o@example.com"):
    h = await signup(ctx, email)
    await upload(ctx, h, "policy.txt", DOC)
    return h


async def _ask(ctx, h, question=Q, history=None):
    return await ctx.post("/chat", headers=h, json={"question": question, "history": history or []})


# ---------------- request IDs and logs ----------------
def test_new_request_id_accepts_sane_values_and_rejects_junk():
    assert new_request_id("abc12345-trace.id") == "abc12345-trace.id"
    for bad in (None, "", "short", "has space in it!", "x" * 65, "evil\r\nHeader: 1", "<script>alert(1)</script>"):
        generated = new_request_id(bad)
        assert generated != bad and len(generated) == 32


async def test_every_response_has_a_request_id_and_valid_ones_are_echoed(ctx):
    r = await ctx.get("/health")
    assert len(r.headers["x-request-id"]) == 32
    r = await ctx.get("/health", headers={"X-Request-ID": "client-supplied-id-1"})
    assert r.headers["x-request-id"] == "client-supplied-id-1"
    r = await ctx.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert r.headers["x-request-id"] != "bad id with spaces"
    # errors and rate limits carry it too
    assert "x-request-id" in (await ctx.post("/chat", json={"question": "hi"})).headers


async def test_json_logs_carry_the_request_id_and_never_the_question(ctx):
    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(self.format(record))

    handler = Capture()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        h = await _user_with_doc(ctx)
        secret_question = "What is the refund policy for my confidential merger?"
        r = await _ask(ctx, h, secret_question)
        rid = r.headers["x-request-id"]
    finally:
        root.removeHandler(handler)

    records = [json.loads(line) for line in lines]
    chat_log = next(x for x in records if x["msg"] == "chat")
    assert chat_log["request_id"] == rid
    assert chat_log["prompt_tokens"] == 120 and chat_log["model"] == FAST and "ttft_ms" in chat_log
    access = next(x for x in records if x["msg"] == "request" and x["request_id"] == rid)
    assert access["route"] == "/chat" and access["status"] == 200 and access["method"] == "POST"
    assert "confidential" not in "".join(lines)  # user text must not leak into logs


def test_json_formatter_includes_extras_and_exceptions():
    token = request_id_var.set("rid-12345678")
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed %s", ("x",), logging.sys.exc_info())
        rec.doc_id = "d1"
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert out["msg"] == "failed x" and out["request_id"] == "rid-12345678" and out["doc_id"] == "d1"
    assert out["level"] == "ERROR" and "ValueError: boom" in out["exc"]


def test_setup_logging_is_idempotent_and_leaves_foreign_handlers_alone():
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        setup_logging("INFO", "json")
        setup_logging("INFO", "json")
        ours = [h for h in root.handlers if getattr(h, "_docchat", False)]
        assert len(ours) == 1 and foreign in root.handlers
    finally:
        root.removeHandler(foreign)


# ---------------- Prometheus metrics ----------------
async def test_metrics_endpoint_exposes_prometheus_text(ctx):
    r = await ctx.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for name in ("chat_ttft_seconds", "chat_requests_total", "llm_tokens_total", "http_requests_total",
                 "documents_ingested_total", "redis_unavailable_total"):
        assert name in r.text


async def test_metrics_can_require_a_bearer_token(ctx, monkeypatch):
    monkeypatch.setattr(settings, "metrics_token", "s3cret")
    assert (await ctx.get("/metrics")).status_code == 401
    assert (await ctx.get("/metrics", headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await ctx.get("/metrics", headers={"Authorization": "Bearer s3cret"})).status_code == 200


async def test_chat_updates_ttft_tokens_and_outcome_metrics(ctx):
    h = await _user_with_doc(ctx)
    app.state.llm.delay = 0.12
    before = {
        "ok": sample("chat_requests_total", outcome="ok"),
        "ttft_n": sample("chat_ttft_seconds_count", source="llm"),
        "ttft_sum": sample("chat_ttft_seconds_sum", source="llm"),
        "prompt": sample("llm_tokens_total", model=FAST, kind="prompt"),
        "completion": sample("llm_tokens_total", model=FAST, kind="completion"),
        "retrieval": sample("chat_retrieval_seconds_count"),
        "rerank": sample("chat_rerank_seconds_count"),
        "total": sample("chat_total_seconds_count"),
    }
    await _ask(ctx, h)
    assert sample("chat_requests_total", outcome="ok") == before["ok"] + 1
    assert sample("chat_ttft_seconds_count", source="llm") == before["ttft_n"] + 1
    assert sample("chat_ttft_seconds_sum", source="llm") - before["ttft_sum"] >= 0.12  # includes model delay
    assert sample("llm_tokens_total", model=FAST, kind="prompt") == before["prompt"] + 120
    assert sample("llm_tokens_total", model=FAST, kind="completion") == before["completion"] + 8
    assert sample("chat_retrieval_seconds_count") == before["retrieval"] + 1
    assert sample("chat_rerank_seconds_count") == before["rerank"] + 1
    assert sample("chat_total_seconds_count") == before["total"] + 1


async def test_cache_hit_not_found_and_quota_outcomes_are_separate(ctx):
    h = await _user_with_doc(ctx)
    await _ask(ctx, h)  # fills the cache

    before = {k: sample("chat_requests_total", outcome=k) for k in ("cached", "not_found", "quota_exceeded")}
    hit, miss = sample("answer_cache_lookups_total", result="hit"), sample("answer_cache_lookups_total", result="miss")
    llm_ttft = sample("chat_ttft_seconds_count", source="llm")

    await _ask(ctx, h)  # cache hit
    await _ask(ctx, h, "quantum chromodynamics lattice gauge")  # nothing relevant
    app.state.quota.daily_limit = 1
    assert (await _ask(ctx, h, "Summarize the shipping information")).status_code == 429

    assert sample("chat_requests_total", outcome="cached") == before["cached"] + 1
    assert sample("chat_requests_total", outcome="not_found") == before["not_found"] + 1
    assert sample("chat_requests_total", outcome="quota_exceeded") == before["quota_exceeded"] + 1
    assert sample("answer_cache_lookups_total", result="hit") == hit + 1
    assert sample("answer_cache_lookups_total", result="miss") >= miss + 2
    # cache and not-found answers never pollute the model-TTFT series
    assert sample("chat_ttft_seconds_count", source="llm") == llm_ttft
    assert sample("chat_ttft_seconds_count", source="cache") >= 1
    assert sample("chat_ttft_seconds_count", source="not_found") >= 1


async def test_llm_failures_count_as_llm_unavailable_and_error(ctx):
    from app.llm.groq_client import LLMUnavailable

    class DownLLM(FakeLLM):
        def __init__(self, exc):
            super().__init__()
            self.exc = exc

        async def stream(self, messages, model, max_tokens, temperature):
            raise self.exc
            yield  # pragma: no cover  (makes this an async generator)

    h = await _user_with_doc(ctx)
    unavailable, error = sample("chat_requests_total", outcome="llm_unavailable"), sample("chat_requests_total", outcome="error")

    app.state.llm = DownLLM(LLMUnavailable("groq 503"))
    events = dict(parse_sse((await _ask(ctx, h)).text))
    assert "temporarily unavailable" in events["error"]["message"]
    assert sample("chat_requests_total", outcome="llm_unavailable") == unavailable + 1

    app.state.llm = DownLLM(RuntimeError("unexpected"))
    await _ask(ctx, h, "Summarize the refund policy")
    assert sample("chat_requests_total", outcome="error") == error + 1


async def test_http_metrics_use_route_templates_not_raw_paths(ctx):
    h = await _user_with_doc(ctx)
    docs = (await ctx.get("/documents", headers=h)).json()
    await ctx.delete(f"/documents/{docs[0]['id']}", headers=h)
    await ctx.get("/some/random/scan/path-123")
    await ctx.get("/another/probe-456")

    assert sample("http_requests_total", method="DELETE", route="/documents/{doc_id}", status="204") >= 1
    assert sample("http_requests_total", method="GET", route="unmatched", status="404") >= 2
    exposition = (await ctx.get("/metrics")).text
    assert docs[0]["id"] not in exposition and "path-123" not in exposition  # bounded cardinality
    assert sample("http_request_duration_seconds_count", method="DELETE", route="/documents/{doc_id}") >= 1


async def test_rate_limited_requests_show_up_as_429s(ctx, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_chat_per_min", 1)
    h = await _user_with_doc(ctx)
    before = sample("http_requests_total", method="POST", route="/chat", status="429")
    await _ask(ctx, h)
    assert (await _ask(ctx, h)).status_code == 429
    assert sample("http_requests_total", method="POST", route="/chat", status="429") == before + 1


async def test_redis_outage_and_reranker_fallback_are_counted(ctx):
    from app.core.cache import AnswerCache
    from tests.test_cache_quota_limits import BrokenRedis

    h = await _user_with_doc(ctx)
    app.state.cache = AnswerCache(BrokenRedis(), 60)
    before = sample("redis_unavailable_total", feature="cache get")
    await _ask(ctx, h)
    assert sample("redis_unavailable_total", feature="cache get") > before

    app.state.reranker.fail = True
    fallbacks = sample("reranker_fallbacks_total")
    await _ask(ctx, h, "Summarize the refund policy")
    assert sample("reranker_fallbacks_total") == fallbacks + 1


# ---------------- OpenTelemetry spans ----------------
async def test_chat_produces_a_connected_span_tree_with_llm_attributes(ctx):
    h = await _user_with_doc(ctx)
    SPANS.clear()
    await _ask(ctx, h)
    spans = spans_by_name()
    root = spans["chat"]
    assert {"retrieval", "embed_query", "vector_search", "rerank", "llm"} <= set(spans)

    assert len({s.context.trace_id for s in spans.values()}) == 1  # one trace
    # (root may itself sit under FastAPI's own request span; that is fine and desirable)
    assert spans["retrieval"].parent.span_id == root.context.span_id
    assert spans["llm"].parent.span_id == root.context.span_id
    for child in ("embed_query", "vector_search", "rerank"):
        assert spans[child].parent.span_id == spans["retrieval"].context.span_id

    llm = spans["llm"]
    assert llm.attributes["gen_ai.request.model"] == FAST
    assert llm.attributes["gen_ai.usage.input_tokens"] == 120
    assert llm.attributes["gen_ai.usage.output_tokens"] == 8
    assert llm.attributes["llm.ttft_ms"] > 0 and any(e.name == "first_token" for e in llm.events)
    assert root.attributes["chat.outcome"] == "ok" and root.attributes["chat.tokens"] == 128
    assert spans["retrieval"].attributes["retrieval.hits"] >= 1
    assert spans["vector_search"].attributes["search.hybrid"] is True


async def test_question_and_answer_text_are_not_traced_by_default(ctx, monkeypatch):
    h = await _user_with_doc(ctx)
    SPANS.clear()
    await _ask(ctx, h, "What is the refund policy for my confidential merger?")
    blob = json.dumps([dict(s.attributes or {}) for s in SPANS.get_finished_spans()])  # ALL spans, FastAPI's too
    assert "confidential" not in blob and "Refunds take" not in blob

    monkeypatch.setattr(settings, "trace_content", True)
    SPANS.clear()
    await _ask(ctx, h, "Summarize the refund policy")
    spans = spans_by_name()
    assert spans["chat"].attributes["input.value"] == "Summarize the refund policy"
    assert spans["llm"].attributes["output.value"] == "Refunds take 14 days [1]."


async def test_follow_up_adds_a_rewrite_span_and_cached_or_not_found_skip_the_llm_span(ctx):
    h = await _user_with_doc(ctx)
    app.state.llm.rewrite_result = "refund policy 14 days"
    hist = [{"role": "user", "content": Q}, {"role": "assistant", "content": "Refunds take 14 days."}]
    SPANS.clear()
    await _ask(ctx, h, "and how long?", hist)
    spans = spans_by_name()
    assert spans["rewrite_query"].parent.span_id == spans["chat"].context.span_id
    assert spans["rewrite_query"].attributes["rewrite.ok"] is True

    SPANS.clear()
    await _ask(ctx, h, "quantum chromodynamics lattice gauge")
    names = {s.name for s in app_spans()}
    assert "llm" not in names and "retrieval" in names

    await _ask(ctx, h)  # fill cache
    SPANS.clear()
    await _ask(ctx, h)
    spans = spans_by_name()
    assert set(spans) == {"chat"} and spans["chat"].attributes["chat.cached"] is True
    assert spans["chat"].attributes["chat.outcome"] == "cached"


async def test_llm_error_marks_the_span_as_failed(ctx):
    class Broken(FakeLLM):
        async def stream(self, messages, model, max_tokens, temperature):
            raise RuntimeError("model exploded")
            yield  # pragma: no cover

    app.state.llm = Broken()
    h = await _user_with_doc(ctx)
    SPANS.clear()
    await _ask(ctx, h)
    spans = spans_by_name()
    assert spans["llm"].status.status_code.name == "ERROR"
    assert any(e.name == "exception" for e in spans["llm"].events)
    assert spans["chat"].attributes["chat.outcome"] == "error"


async def test_client_disconnect_is_recorded_as_aborted_and_spans_still_end(ctx):
    store, emb = app.state.store, FakeEmbedder()
    await store.upsert_chunks(
        user_id="u-abort", doc_id="d1", filename="p.txt", chunks=[Chunk(0, 1, DOC.decode())],
        vectors=await emb.embed([DOC.decode()]),
    )
    app.state.llm = FakeLLM(tokens=tuple(f" w{i}" for i in range(50)))
    request = Request({"type": "http", "app": app, "method": "POST", "path": "/chat", "headers": [], "query_string": b""})
    resp = await chat(ChatRequest(question=Q), request, User(id="u-abort", email="x@example.com", password_hash="x"))

    aborted = sample("chat_requests_total", outcome="aborted")
    SPANS.clear()
    body = resp.body_iterator
    async for piece in body:
        if "event: token" in piece:
            break
    await body.aclose()

    assert sample("chat_requests_total", outcome="aborted") == aborted + 1
    spans = spans_by_name()
    assert spans["chat"].attributes["chat.outcome"] == "aborted" and "llm" in spans  # nothing left open


# ---------------- worker: ingestion spans + metrics ----------------
def _run(doc_id, **kw):
    s = app.state
    return process_document(
        doc_id, session_factory=SessionLocal, storage=s.storage, embedder=s.embedder, store=s.store,
        settings=settings, **kw,
    )


async def test_ingestion_emits_spans_and_ready_metrics(ctx, monkeypatch):
    monkeypatch.setattr(settings, "ingest_mode", "celery")
    monkeypatch.setattr("app.api.documents.enqueue_ingestion", lambda doc_id: None)
    h = await signup(ctx, "w1@example.com")
    doc_id = (await upload(ctx, h, "policy.txt", DOC)).json()["id"]

    ready, durations = sample("documents_ingested_total", status="ready"), sample("ingest_duration_seconds_count")
    SPANS.clear()
    await _run(doc_id)

    assert sample("documents_ingested_total", status="ready") == ready + 1
    assert sample("ingest_duration_seconds_count") == durations + 1
    spans = spans_by_name()
    root = spans["ingest_document"]
    assert {"parse", "embed", "upsert"} <= set(spans)
    assert all(spans[n].parent.span_id == root.context.span_id for n in ("parse", "embed", "upsert"))
    assert root.attributes["doc.chunks"] >= 1 and root.attributes["doc.id"] == doc_id


async def test_failed_ingestion_is_counted_and_span_marked(ctx, monkeypatch):
    monkeypatch.setattr(settings, "ingest_mode", "celery")
    monkeypatch.setattr("app.api.documents.enqueue_ingestion", lambda doc_id: None)
    h = await signup(ctx, "w2@example.com")
    doc_id = (await upload(ctx, h, "broken.pdf", b"%PDF-1.4\n not really a pdf")).json()["id"]
    failed = sample("documents_ingested_total", status="failed")
    SPANS.clear()
    await _run(doc_id)
    assert sample("documents_ingested_total", status="failed") == failed + 1
    assert spans_by_name()["ingest_document"].status.status_code.name == "ERROR"


async def test_retryable_error_is_not_counted_until_the_final_attempt(ctx, monkeypatch):
    class Flaky(FakeEmbedder):
        async def embed(self, texts):
            raise ConnectionError("hiccup")

    monkeypatch.setattr(settings, "ingest_mode", "celery")
    monkeypatch.setattr("app.api.documents.enqueue_ingestion", lambda doc_id: None)
    h = await signup(ctx, "w3@example.com")
    doc_id = (await upload(ctx, h, "policy.txt", DOC)).json()["id"]
    failed = sample("documents_ingested_total", status="failed")
    with pytest.raises(ConnectionError):
        await process_document(
            doc_id, session_factory=SessionLocal, storage=app.state.storage, embedder=Flaky(),
            store=app.state.store, settings=settings, final_attempt=False,
        )
    assert sample("documents_ingested_total", status="failed") == failed  # Celery will retry


def test_ocr_pages_are_counted(monkeypatch):
    monkeypatch.setattr(tesseract, "ocr_pdf_page", lambda *a, **k: "text")
    before = sample("ocr_pages_total")
    ocr = tesseract.make_ocr("/tmp/x.pdf", settings)
    ocr(1), ocr(2)
    assert sample("ocr_pages_total") == before + 2


def test_storage_import_is_unaffected():  # guards the LocalStorage import used by sibling tests
    assert LocalStorage
