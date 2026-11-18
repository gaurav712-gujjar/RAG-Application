import asyncio

import pytest
from starlette.requests import Request

from app.api.chat import ChatRequest, chat
from app.chunking.splitter import Chunk
from app.core.cache import AnswerCache
from app.core.config import get_settings
from app.core.limits import RateLimiter, TokenQuota
from app.llm.groq_client import LLMEvent
from app.main import app
from app.models import User
from tests.fakes import FakeEmbedder, FakeLLM
from tests.helpers import parse_sse, signup, upload

settings = get_settings()
DOC = b"Refunds are processed within 14 days. The refund policy covers all purchases."
Q = "What is the refund policy?"
REAL_TOKENS = 120 + 8  # FakeLLM's reported usage


async def _ask(ctx, headers, question=Q, history=None):
    return await ctx.post("/chat", headers=headers, json={"question": question, "history": history or []})


async def _user_with_doc(ctx, email="c@example.com"):
    h = await signup(ctx, email)
    await upload(ctx, h, "policy.txt", DOC)
    return h


async def _used(ctx, h) -> int:
    return (await ctx.get("/usage", headers=h)).json()["used_today"]


# ---------------- answer cache ----------------
async def test_repeated_question_is_served_from_cache_without_llm_or_tokens(ctx):
    h = await _user_with_doc(ctx)
    first = dict(parse_sse((await _ask(ctx, h)).text))
    assert app.state.llm.calls == 1 and first["done"]["cached"] is False
    used_after_first = await _used(ctx, h)
    assert used_after_first == REAL_TOKENS

    second_events = parse_sse((await _ask(ctx, h, "  what IS the refund   policy ")).text)  # normalised
    second = dict(second_events)
    assert app.state.llm.calls == 1  # no new LLM call
    assert second["ttft"]["cached"] is True and second["done"]["cached"] is True
    assert second["done"]["prompt_tokens"] == 0
    assert second["token"]["t"] == "Refunds take 14 days [1]."
    assert second["sources"] == first["sources"]
    assert await _used(ctx, h) == used_after_first  # cache hits are free


async def test_follow_ups_are_never_cached(ctx):
    h = await _user_with_doc(ctx)
    await _ask(ctx, h)
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    await _ask(ctx, h, history=hist)  # same question text, but inside a conversation
    assert app.state.llm.calls == 2


async def test_cache_is_per_user(ctx):
    a = await _user_with_doc(ctx, "a@example.com")
    b = await signup(ctx, "b@example.com")
    await _ask(ctx, a)
    events = dict(parse_sse((await _ask(ctx, b)).text))
    assert events["sources"] == [] and events["done"]["cached"] is False  # B never sees A's answer


async def test_upload_and_delete_invalidate_cached_answers(ctx):
    h = await _user_with_doc(ctx)
    await _ask(ctx, h)
    await _ask(ctx, h)
    assert app.state.llm.calls == 1

    new_doc = (await upload(ctx, h, "more.txt", b"Shipping takes 3 days. Refund shipping fees apply.")).json()
    await _ask(ctx, h)
    assert app.state.llm.calls == 2  # new content -> old answer may be stale -> recomputed

    await _ask(ctx, h)
    assert app.state.llm.calls == 2  # cached again
    await ctx.delete(f"/documents/{new_doc['id']}", headers=h)
    await _ask(ctx, h)
    assert app.state.llm.calls == 3  # deletion also invalidates


async def test_not_found_answers_are_not_cached_and_cost_nothing(ctx):
    h = await signup(ctx, "nf@example.com")
    await _ask(ctx, h)
    await _ask(ctx, h)
    assert app.state.llm.calls == 0 and await _used(ctx, h) == 0


async def test_cache_can_be_disabled(ctx):
    h = await _user_with_doc(ctx)
    app.state.cache = None
    await _ask(ctx, h)
    await _ask(ctx, h)
    assert app.state.llm.calls == 2


# ---------------- token quota ----------------
async def test_usage_endpoint_reports_tokens(ctx):
    h = await _user_with_doc(ctx)
    assert (await ctx.get("/usage")).status_code == 401
    before = (await ctx.get("/usage", headers=h)).json()
    assert before["used_today"] == 0 and before["daily_limit"] == settings.daily_token_quota
    await _ask(ctx, h)
    assert await _used(ctx, h) == REAL_TOKENS


async def test_quota_blocks_new_questions_but_still_serves_cache(ctx):
    h = await _user_with_doc(ctx)
    await _ask(ctx, h)  # uses 128 tokens and fills the cache
    app.state.quota.daily_limit = 100

    blocked = await _ask(ctx, h, "Summarize the shipping information")
    assert blocked.status_code == 429
    assert "Daily token limit" in blocked.json()["detail"]
    assert int(blocked.headers["Retry-After"]) > 0

    assert (await _ask(ctx, h)).status_code == 200  # cached answer: free, so allowed

    app.state.quota.daily_limit = 0  # unlimited
    assert (await _ask(ctx, h, "Summarize the shipping information")).status_code == 200


async def test_quota_is_per_user(ctx):
    a = await _user_with_doc(ctx, "qa@example.com")
    b = await _user_with_doc(ctx, "qb@example.com")
    await _ask(ctx, a)
    app.state.quota.daily_limit = 100
    assert (await _ask(ctx, a, "Summarize it")).status_code == 429
    assert (await _ask(ctx, b)).status_code == 200


async def test_rewrite_tokens_count_toward_quota(ctx):
    h = await _user_with_doc(ctx)
    hist = [{"role": "user", "content": Q}, {"role": "assistant", "content": "Refunds take 14 days."}]
    app.state.llm.rewrite_result = "refund policy 14 days"  # so retrieval finds the doc and the LLM answers
    await _ask(ctx, h, "and how long?", hist)
    assert await _used(ctx, h) == 30 + 5 + REAL_TOKENS  # rewrite call + answer


async def test_missing_usage_from_llm_is_estimated(ctx):
    class NoUsageLLM(FakeLLM):
        async def stream(self, messages, model, max_tokens, temperature):
            async for ev in super().stream(messages, model, max_tokens, temperature):
                if ev.kind != "usage":
                    yield ev

    app.state.llm = NoUsageLLM()
    h = await _user_with_doc(ctx)
    await _ask(ctx, h)
    assert await _used(ctx, h) > 0


async def test_client_disconnect_still_charges_an_estimate(ctx):
    store, emb = app.state.store, FakeEmbedder()
    text = "Refunds are processed within 14 days. The refund policy covers all purchases."
    await store.upsert_chunks(
        user_id="u-disc", doc_id="d1", filename="p.txt",
        chunks=[Chunk(0, 1, text)], vectors=await emb.embed([text]),
    )
    app.state.llm = FakeLLM(tokens=tuple(f" word{i}" for i in range(50)))
    request = Request({"type": "http", "app": app, "method": "POST", "path": "/chat", "headers": [], "query_string": b""})
    resp = await chat(ChatRequest(question=Q), request, User(id="u-disc", email="x@example.com", password_hash="x"))

    body = resp.body_iterator
    async for chunk in body:
        if "event: token" in chunk:
            break  # client walks away after the first token
    await body.aclose()

    used = await app.state.quota.used("u-disc")
    assert used > 0  # the prompt was paid for even though the answer was cut off


# ---------------- rate limiting ----------------
async def test_chat_rate_limit_is_per_user_with_retry_after(ctx, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_chat_per_min", 2)
    a = await _user_with_doc(ctx, "ra@example.com")
    b = await _user_with_doc(ctx, "rb@example.com")
    assert (await _ask(ctx, a)).status_code == 200
    assert (await _ask(ctx, a)).status_code == 200
    third = await _ask(ctx, a)
    assert third.status_code == 429 and 1 <= int(third.headers["Retry-After"]) <= 60
    assert (await _ask(ctx, b)).status_code == 200  # another user is unaffected


async def test_upload_rate_limit(ctx, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_upload_per_min", 2)
    h = await signup(ctx, "up@example.com")
    assert (await upload(ctx, h, "a.txt", b"first file content")).status_code == 201
    assert (await upload(ctx, h, "b.txt", b"second file content")).status_code == 201
    assert (await upload(ctx, h, "c.txt", b"third file content")).status_code == 429


async def test_login_rate_limit_blocks_password_guessing(ctx, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_auth_per_min", 3)
    await signup(ctx, "victim@example.com")  # counts as 1 hit for this IP
    body = {"email": "victim@example.com", "password": "wrong-guess-1"}
    assert (await ctx.post("/auth/login", json=body)).status_code == 401
    assert (await ctx.post("/auth/login", json=body)).status_code == 401
    blocked = await ctx.post("/auth/login", json=body)
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers


async def test_unauthenticated_requests_do_not_consume_user_limits(ctx):
    assert (await ctx.post("/chat", json={"question": "hi"})).status_code == 401


async def test_sliding_window_frees_up_after_the_window(ctx):
    limiter = RateLimiter(app.state.redis)
    assert await limiter.hit("k", 2, 1) is None
    assert await limiter.hit("k", 2, 1) is None
    wait = await limiter.hit("k", 2, 1)
    assert wait == 1
    await asyncio.sleep(1.1)
    assert await limiter.hit("k", 2, 1) is None


# ---------------- Redis down: fail open ----------------
class BrokenRedis:
    def __getattr__(self, name):
        def boom(*a, **k):
            raise ConnectionError("redis is down")

        return boom


async def test_redis_outage_does_not_break_chat_or_uploads(ctx):
    h = await _user_with_doc(ctx, "down@example.com")
    broken = BrokenRedis()
    app.state.cache = AnswerCache(broken, 60)
    app.state.quota = TokenQuota(broken, 10)  # tiny limit would block if Redis worked
    app.state.limiter = RateLimiter(broken)

    r = await _ask(ctx, h)
    assert r.status_code == 200
    answer = "".join(d["t"] for e, d in parse_sse(r.text) if e == "token")
    assert answer == "Refunds take 14 days [1]."
    assert (await upload(ctx, h, "x.txt", b"another document body")).status_code == 201
    assert (await ctx.get("/usage", headers=h)).json()["used_today"] == 0
