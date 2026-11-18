import pytest

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.storage import LocalStorage
from app.main import app
from app.models import Document
from app.workers.pipeline import process_document
from tests.fakes import FakeEmbedder, make_pdf_pages
from tests.helpers import parse_sse, signup, upload

settings = get_settings()
DOC = b"Refunds are processed within 14 days. The refund policy covers all purchases."


@pytest.fixture
def queue(monkeypatch):
    """Switch to queued mode and capture what would be sent to Celery."""
    sent: list[str] = []
    monkeypatch.setattr(settings, "ingest_mode", "celery")
    monkeypatch.setattr("app.api.documents.enqueue_ingestion", sent.append)
    return sent


def _run(doc_id, **kw):
    s = app.state
    return process_document(
        doc_id, session_factory=SessionLocal, storage=s.storage, embedder=kw.pop("embedder", s.embedder),
        store=s.store, settings=settings, **kw,
    )


async def test_queued_upload_returns_202_then_worker_makes_it_ready(ctx, queue):
    h = await signup(ctx, "q1@example.com")
    r = await upload(ctx, h, "policy.txt", DOC)
    assert r.status_code == 202
    doc = r.json()
    assert doc["status"] == "uploaded" and doc["num_chunks"] == 0
    assert queue == [doc["id"]]

    # not searchable yet
    r1 = await ctx.post("/chat", headers=h, json={"question": "What is the refund policy?"})
    assert dict(parse_sse(r1.text))["sources"] == []

    await _run(doc["id"])  # what the Celery worker does
    listed = (await ctx.get("/documents", headers=h)).json()[0]
    assert listed["status"] == "ready" and listed["num_chunks"] >= 1

    r2 = await ctx.post("/chat", headers=h, json={"question": "What is the refund policy?"})
    assert dict(parse_sse(r2.text))["sources"]


async def test_processing_is_idempotent(ctx, queue):
    h = await signup(ctx, "q2@example.com")
    doc_id = (await upload(ctx, h, "policy.txt", DOC)).json()["id"]
    await _run(doc_id)
    await _run(doc_id)  # redelivered message: skipped, no duplicate vectors
    user_id = (await ctx.get("/auth/me", headers=h)).json()["id"]
    vec = (await FakeEmbedder().embed(["refund policy"]))[0]
    hits = await app.state.store.search(user_id, vec, 20)
    assert len(hits) == (await ctx.get("/documents", headers=h)).json()[0]["num_chunks"]


async def test_enqueue_failure_returns_503_and_marks_failed(ctx, monkeypatch):
    def boom(doc_id):
        raise ConnectionError("redis down")

    monkeypatch.setattr(settings, "ingest_mode", "celery")
    monkeypatch.setattr("app.api.documents.enqueue_ingestion", boom)
    h = await signup(ctx, "q3@example.com")
    r = await upload(ctx, h, "policy.txt", DOC)
    assert r.status_code == 503
    doc = (await ctx.get("/documents", headers=h)).json()[0]
    assert doc["status"] == "failed" and "queue" in doc["error"].lower()


async def test_transient_error_retries_then_fails_cleanly(ctx, queue):
    class FlakyEmbedder(FakeEmbedder):
        async def embed(self, texts):
            raise ConnectionError("qdrant/embedder hiccup")

    h = await signup(ctx, "q4@example.com")
    doc_id = (await upload(ctx, h, "policy.txt", DOC)).json()["id"]

    with pytest.raises(ConnectionError):  # not final -> bubbles up so Celery retries
        await _run(doc_id, embedder=FlakyEmbedder(), final_attempt=False)
    assert (await ctx.get("/documents", headers=h)).json()[0]["status"] == "processing"

    await _run(doc_id, embedder=FlakyEmbedder(), final_attempt=True)  # last attempt -> failed
    doc = (await ctx.get("/documents", headers=h)).json()[0]
    assert doc["status"] == "failed" and doc["error"]
    assert not list(LocalStorage(settings.upload_dir).root.rglob(f"{doc_id}.*"))  # file cleaned up


async def test_parse_error_fails_immediately_without_retry(ctx, queue):
    h = await signup(ctx, "q5@example.com")
    doc_id = (await upload(ctx, h, "broken.pdf", b"%PDF-1.4\n not really a pdf")).json()["id"]
    await _run(doc_id, final_attempt=False)  # ParseError must not raise even if not final
    doc = (await ctx.get("/documents", headers=h)).json()[0]
    assert doc["status"] == "failed" and "PDF" in doc["error"]


async def test_scanned_pdf_goes_through_ocr_fallback(ctx, queue):
    h = await signup(ctx, "q6@example.com")
    pdf = make_pdf_pages(["", "Printed page with refund policy text that is long enough to skip OCR entirely"])
    doc_id = (await upload(ctx, h, "scan.pdf", pdf)).json()["id"]
    await _run(doc_id, ocr=lambda n: "Scanned refund policy says refunds take 14 days to process")

    doc = (await ctx.get("/documents", headers=h)).json()[0]
    assert doc["status"] == "ready"
    r = await ctx.post("/chat", headers=h, json={"question": "How long do refunds take?"})
    sources = dict(parse_sse(r.text))["sources"]
    assert {s["page"] for s in sources} == {1, 2}  # OCR'd page 1 and text page 2 both searchable


async def test_scanned_pdf_without_tesseract_fails_with_clear_reason(ctx, queue):
    """Real OCR callable in an environment without the tesseract binary -> readable error, no crash."""
    h = await signup(ctx, "q7@example.com")
    doc_id = (await upload(ctx, h, "scan.pdf", make_pdf_pages([""]))).json()["id"]

    def missing(n):
        raise RuntimeError("tesseract is not installed or it's not in your PATH")

    await _run(doc_id, ocr=missing)
    doc = (await ctx.get("/documents", headers=h)).json()[0]
    assert doc["status"] == "failed" and "OCR failed" in doc["error"]


async def test_delete_before_worker_runs_is_a_noop(ctx, queue):
    h = await signup(ctx, "q8@example.com")
    doc_id = (await upload(ctx, h, "policy.txt", DOC)).json()["id"]
    assert (await ctx.delete(f"/documents/{doc_id}", headers=h)).status_code == 204
    await _run(doc_id)  # worker picks up a deleted doc: must not crash or write vectors
    user_id = (await ctx.get("/auth/me", headers=h)).json()["id"]
    vec = (await FakeEmbedder().embed(["refund policy"]))[0]
    assert await app.state.store.search(user_id, vec, 20) == []


async def test_delete_during_processing_cleans_up_vectors(ctx, queue):
    h = await signup(ctx, "q9@example.com")
    doc_id = (await upload(ctx, h, "policy.txt", DOC)).json()["id"]

    class DeletesMidFlight(FakeEmbedder):
        async def embed(self, texts):
            vectors = await super().embed(texts)
            async with SessionLocal() as s:  # user hits "delete" while embedding is running
                await s.delete(await s.get(Document, doc_id))
                await s.commit()
            return vectors

    await _run(doc_id, embedder=DeletesMidFlight())
    user_id = (await ctx.get("/auth/me", headers=h)).json()["id"]
    vec = (await FakeEmbedder().embed(["refund policy"]))[0]
    assert await app.state.store.search(user_id, vec, 20) == []  # nothing orphaned in Qdrant
    assert not list(LocalStorage(settings.upload_dir).root.rglob(f"{doc_id}.*"))


def test_local_storage_rejects_path_traversal(tmp_path):
    st = LocalStorage(str(tmp_path / "root"))
    st.ensure_ready()
    st.save("u1/a.txt", b"ok")
    for bad in ("../evil.txt", "/etc/passwd", "u1/../../evil.txt"):
        with pytest.raises(ValueError):
            st.save(bad, b"x")


def test_local_storage_roundtrip_and_idempotent_delete(tmp_path):
    st = LocalStorage(str(tmp_path / "root"))
    st.ensure_ready()
    st.save("u1/a.txt", b"hello")
    dest = tmp_path / "out.txt"
    st.download("u1/a.txt", str(dest))
    assert dest.read_bytes() == b"hello"
    st.delete("u1/a.txt")
    st.delete("u1/a.txt")  # second delete is fine
