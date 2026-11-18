from pathlib import Path

from tests.fakes import make_docx, make_pdf
from tests.helpers import signup, upload


async def test_register_login_me(ctx):
    r = await ctx.post("/auth/register", json={"email": "Dave@Example.com", "password": "password123"})
    assert r.status_code == 201
    assert (await ctx.post("/auth/register", json={"email": "dave@example.com", "password": "password123"})).status_code == 409
    bad = await ctx.post("/auth/login", json={"email": "dave@example.com", "password": "wrong-pass-1"})
    assert bad.status_code == 401
    ok = await ctx.post("/auth/login", json={"email": "dave@example.com", "password": "password123"})
    me = await ctx.get("/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"})
    assert me.json()["email"] == "dave@example.com"


async def test_endpoints_require_auth(ctx):
    assert (await ctx.get("/documents")).status_code == 401
    assert (await ctx.post("/chat", json={"question": "hi"})).status_code == 401
    assert (await ctx.get("/documents", headers={"Authorization": "Bearer garbage"})).status_code == 401


async def test_upload_supported_types(ctx):
    h = await signup(ctx, "erin@example.com")
    for name, data in [
        ("a.txt", b"plain text content here"),
        ("b.md", b"# Title\n\nSome markdown content"),
        ("c.docx", make_docx("docx content about invoices")),
        ("d.pdf", make_pdf("pdf content about shipping")),
    ]:
        r = await upload(ctx, h, name, data)
        assert r.status_code == 201, (name, r.text)
        assert r.json()["status"] == "ready" and r.json()["num_chunks"] >= 1
    assert len((await ctx.get("/documents", headers=h)).json()) == 4


async def test_upload_rejections(ctx):
    h = await signup(ctx, "frank@example.com")
    assert (await upload(ctx, h, "empty.txt", b"")).status_code == 400
    assert (await upload(ctx, h, "evil.exe", b"MZ....")).status_code == 415
    assert (await upload(ctx, h, "fake.pdf", b"just text pretending")).status_code == 415  # magic bytes
    assert (await upload(ctx, h, "fake.docx", b"not a zip")).status_code == 415
    assert (await upload(ctx, h, "bin.txt", b"abc\x00\x01\x02")).status_code == 415
    assert (await upload(ctx, h, "big.txt", b"a" * (1024 * 1024 + 10))).status_code == 413  # MAX_UPLOAD_MB=1


async def test_corrupt_pdf_marked_failed_and_cleaned_up(ctx):
    h = await signup(ctx, "gina@example.com")
    r = await upload(ctx, h, "broken.pdf", b"%PDF-1.4\n garbage that is not a pdf")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "failed" and body["error"]
    # a failed upload leaves no file behind (other tests' files share this dir, so match by doc id)
    from app.core.config import get_settings

    assert not list(Path(get_settings().upload_dir).rglob(f"{body['id']}.*"))


async def test_duplicate_upload_returns_existing(ctx):
    h = await signup(ctx, "hank@example.com")
    first = await upload(ctx, h, "a.txt", b"same bytes same bytes")
    second = await upload(ctx, h, "renamed.txt", b"same bytes same bytes")
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len((await ctx.get("/documents", headers=h)).json()) == 1


async def test_filename_is_sanitized(ctx):
    h = await signup(ctx, "ivy@example.com")
    r = await upload(ctx, h, "../../etc/pass<wd>.txt", b"hello world content")
    assert r.status_code == 201
    assert "/" not in r.json()["filename"] and "<" not in r.json()["filename"]


async def test_delete_cascades_to_vectors_file_and_row(ctx):
    from app.main import app
    from tests.fakes import FakeEmbedder

    h = await signup(ctx, "jo@example.com")
    doc = (await upload(ctx, h, "a.txt", b"refund policy details here")).json()
    user_id = (await ctx.get("/auth/me", headers=h)).json()["id"]
    vec = (await FakeEmbedder().embed(["refund policy details"]))[0]
    assert await app.state.store.search(user_id, vec, 8)

    assert (await ctx.delete(f"/documents/{doc['id']}", headers=h)).status_code == 204
    assert await app.state.store.search(user_id, vec, 8) == []
    assert (await ctx.get("/documents", headers=h)).json() == []
    from app.core.config import get_settings

    assert not list(Path(get_settings().upload_dir).rglob(f"{doc['id']}.*"))
