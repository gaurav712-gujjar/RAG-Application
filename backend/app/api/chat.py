import json
import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user
from app.core import metrics
from app.core.config import get_settings
from app.core.limits import QuotaExceeded, limit_chat
from app.core.tracing import SpanKind, fail, trace, tracer
from app.llm.groq_client import LLMUnavailable
from app.llm.prompts import (
    NO_WEB_RESULTS, NOT_FOUND, build_messages, build_web_messages, choose_model, retrieval_query, trim_history,
)
from app.llm.rewrite import needs_rewrite, rewrite_query
from app.models import User
from app.retrieval.search import retrieve
from app.websearch.search import web_search

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(tags=["chat"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=20)
    # docs = only your documents; docs_web = documents, then the web if nothing is found; web = web only
    mode: Literal["docs", "docs_web", "web"] = "docs"
    doc_ids: list[str] | None = Field(default=None, max_length=50)  # limit the search to these documents
    # NOTE: no user_id here on purpose. Identity comes only from the JWT.


def sse(event: str, data: dict | list) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _est_tokens(chars: int) -> int:
    return max(1, chars // 4)


@router.post("/chat", dependencies=[Depends(limit_chat)])
async def chat(req: ChatRequest, request: Request, user: User = Depends(get_current_user)):
    # TTFT starts when the request arrived (set by the middleware), so auth and rate limiting count.
    t0 = getattr(request.state, "t0", None) or time.perf_counter()
    state = request.app.state
    user_id = user.id
    cache = getattr(state, "cache", None)
    quota = getattr(state, "quota", None)
    reranker = getattr(state, "reranker", None)

    # Only first questions are cached: a follow-up's meaning depends on the conversation.
    use_cache = cache is not None and not req.history and req.mode == "docs" and not req.doc_ids
    cached = await cache.get(user_id, req.question) if use_cache else None
    if use_cache:
        metrics.CACHE_LOOKUPS.labels("hit" if cached is not None else "miss").inc()

    # Cached answers cost no tokens, so they are served even when the quota is used up.
    if cached is None and quota is not None:
        try:
            await quota.check(user_id)
        except QuotaExceeded as e:
            metrics.CHAT_REQUESTS.labels("quota_exceeded").inc()
            hours, rem = divmod(e.retry_after, 3600)
            raise HTTPException(
                429,
                f"Daily token limit reached ({e.used:,} of {e.limit:,}). "
                f"Resets in {hours}h {rem // 60}m.",
                headers={"Retry-After": str(e.retry_after)},
            )

    async def events():
        # Spans are created and ended by hand (not as context managers): this generator yields
        # between steps, and OpenTelemetry's context attach/detach must not straddle a yield.
        root = tracer.start_span(
            "chat", attributes={"user.id": user_id, "chat.cached": cached is not None,
                                "chat.follow_up": bool(req.history)},
        )
        ctx = trace.set_span_in_context(root)
        if settings.trace_content:
            root.set_attribute("input.value", req.question)

        outcome = "aborted"  # stays this if the client disconnects before we finish
        spent = 0  # tokens fully accounted for (rewrite call + finished answer)
        streaming_est = 0  # estimate charged if the stream is cut short (client disconnect)
        partial_chars = 0
        llm_span = None
        try:
            if cached is not None:
                ttft_ms = _ms(t0)
                outcome = "cached"
                metrics.CHAT_TTFT.labels("cache").observe(ttft_ms / 1000)
                yield sse("sources", cached["sources"])
                yield sse("ttft", {"ttft_ms": ttft_ms, "retrieval_ms": 0, "cached": True})
                yield sse("token", {"t": cached["answer"]})
                yield sse(
                    "done",
                    {"ttft_ms": ttft_ms, "retrieval_ms": 0, "rerank_ms": 0, "total_ms": _ms(t0),
                     "model": cached.get("model"), "prompt_tokens": 0, "completion_tokens": 0, "cached": True},
                )
                return

            history = trim_history(req.history, settings.history_turns)
            yield sse("stage", {"name": "searching"})  # instant feedback while we embed/search/rerank

            search_query = retrieval_query(req.question, history)  # cheap heuristic fallback
            is_follow_up = bool(history) and needs_rewrite(req.question, history)
            if settings.rewrite_enabled and is_follow_up:
                with tracer.start_as_current_span("rewrite_query", context=ctx) as rs:
                    rewritten, rewrite_tokens = await rewrite_query(state.llm, settings, req.question, history)
                    rs.set_attribute("gen_ai.usage.total_tokens", rewrite_tokens)
                    rs.set_attribute("rewrite.ok", rewritten is not None)
                spent += rewrite_tokens
                if rewrite_tokens:
                    metrics.LLM_TOKENS.labels(settings.llm_model_fast, "rewrite").inc(rewrite_tokens)
                search_query = rewritten or search_query  # on failure keep the heuristic query

            prep_ms = _ms(t0)  # everything before retrieval: auth, limits, cache, quota, rewrite
            r = None
            retrieval_ms = rerank_ms = 0.0
            hits: list = []
            sources: list = []
            breakdown = {"considered": [], "prep_ms": prep_ms, "embed_ms": 0, "search_ms": 0}
            if req.mode != "web":
                r = await retrieve(state.embedder, state.store, reranker, user_id, search_query, settings,
                                   otel_ctx=ctx, doc_ids=req.doc_ids)
                retrieval_ms, rerank_ms = round(r.retrieval_ms, 1), round(r.rerank_ms, 1)
                metrics.CHAT_RETRIEVAL.observe(r.retrieval_ms / 1000)
                if r.rerank_ms:
                    metrics.CHAT_RERANK.observe(r.rerank_ms / 1000)
                hits = r.hits
                sources = [
                    {"n": i, "filename": h.filename, "page": h.page, "doc_id": h.doc_id, "score": round(h.score, 3)}
                    for i, h in enumerate(hits, start=1)
                ]
                yield sse("sources", sources)
                considered = [
                    {"filename": h.filename, "page": h.page, "score": round(h.score, 3),
                     "kept": any(h is k for k in hits)}
                    for h in (r.considered or [])[:5]
                ]
                breakdown = {"considered": considered, "prep_ms": prep_ms, "embed_ms": round(r.embed_ms, 1),
                             "search_ms": round(r.search_ms, 1)}

            use_web = req.mode == "web" or (req.mode == "docs_web" and not hits)
            web_results: list = []
            if use_web:
                yield sse("stage", {"name": "web"})
                t_web = time.perf_counter()
                web_results = await web_search(
                    search_query,
                    max_results=getattr(settings, "web_search_results", 5),
                    region=getattr(settings, "web_search_region", "in-en"),
                )
                breakdown["web_ms"] = _ms(t_web)
                sources = [
                    {"n": i, "kind": "web", "filename": w.title, "url": w.url, "page": None, "doc_id": None}
                    for i, w in enumerate(web_results, start=1)
                ]
                yield sse("sources", sources)

            # Nothing relevant: skip the LLM entirely (saves tokens and avoids hallucination).
            if (use_web and not web_results) or (not use_web and not hits):
                outcome = "not_found"
                ttft_ms = _ms(t0)
                metrics.CHAT_TTFT.labels("not_found").observe(ttft_ms / 1000)
                yield sse("ttft", {"ttft_ms": ttft_ms, "retrieval_ms": retrieval_ms, "cached": False})
                yield sse("token", {"t": NO_WEB_RESULTS if use_web else NOT_FOUND})
                yield sse(
                    "done",
                    {"ttft_ms": ttft_ms, "retrieval_ms": retrieval_ms, "rerank_ms": rerank_ms, **breakdown,
                     "total_ms": _ms(t0), "model": None, "prompt_tokens": 0, "completion_tokens": 0,
                     "cached": False, "answer_source": "web" if use_web else "docs",
                     "web_available": not use_web},
                )
                return

            yield sse("stage", {"name": "writing"})
            model = choose_model(req.question, settings)
            # A standalone question gets no chat history: it saves tokens and stops the model from
            # repeating its previous answer (small models do that when history is present).
            answer_history = history if is_follow_up else []
            if use_web:
                messages = build_web_messages(req.question, web_results, answer_history)
            else:
                messages = build_messages(req.question, hits, answer_history)
            est_prompt = _est_tokens(sum(len(m["content"]) for m in messages))
            streaming_est = est_prompt
            ttft_ms: float | None = None
            prompt_tokens = completion_tokens = None
            answer_parts: list[str] = []

            llm_span = tracer.start_span(
                "llm", context=ctx, kind=SpanKind.CLIENT,
                attributes={"gen_ai.system": "groq", "gen_ai.request.model": model,
                            "gen_ai.request.max_tokens": settings.max_output_tokens},
            )
            async for ev in state.llm.stream(
                messages, model, settings.max_output_tokens, settings.temperature
            ):
                if ev.kind == "token" and ev.text:
                    if ttft_ms is None:  # first non-empty token
                        ttft_ms = _ms(t0)
                        metrics.CHAT_TTFT.labels("llm").observe(ttft_ms / 1000)
                        llm_span.add_event("first_token", {"ttft_ms": ttft_ms})
                        yield sse(
                            "ttft", {"ttft_ms": ttft_ms, "retrieval_ms": retrieval_ms, "cached": False}
                        )
                    answer_parts.append(ev.text)
                    partial_chars += len(ev.text)
                    yield sse("token", {"t": ev.text})
                elif ev.kind == "usage":
                    prompt_tokens, completion_tokens = ev.prompt_tokens, ev.completion_tokens

            answer = "".join(answer_parts)
            if prompt_tokens is None:
                prompt_tokens = est_prompt
            if completion_tokens is None:
                completion_tokens = _est_tokens(len(answer))
            spent += prompt_tokens + completion_tokens
            streaming_est = 0  # finished normally: real numbers replace the estimate
            metrics.LLM_TOKENS.labels(model, "prompt").inc(prompt_tokens)
            metrics.LLM_TOKENS.labels(model, "completion").inc(completion_tokens)
            llm_span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
            llm_span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
            if ttft_ms is not None:
                llm_span.set_attribute("llm.ttft_ms", ttft_ms)
            if settings.trace_content:
                llm_span.set_attribute("output.value", answer)

            if use_cache and answer:
                await cache.set(
                    user_id, req.question, {"answer": answer, "sources": sources, "model": model}
                )

            outcome = "ok"
            total_ms = _ms(t0)
            logger.info(
                "chat",
                extra={"user_id": user_id, "model": model, "ttft_ms": ttft_ms, "retrieval_ms": retrieval_ms,
                       "rerank_ms": rerank_ms, "total_ms": total_ms,
                       "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            )
            yield sse(
                "done",
                {"ttft_ms": ttft_ms, "retrieval_ms": retrieval_ms, "rerank_ms": rerank_ms, **breakdown,
                 "total_ms": total_ms, "model": model, "prompt_tokens": prompt_tokens,
                 "completion_tokens": completion_tokens, "cached": False,
                 "answer_source": "web" if use_web else "docs"},
            )
        except LLMUnavailable as exc:
            outcome = "llm_unavailable"
            logger.exception("LLM unavailable")
            fail(llm_span or root, exc)
            yield sse("error", {"message": "The AI service is temporarily unavailable. Please try again."})
        except Exception as exc:
            outcome = "error"
            logger.exception("chat failed")
            fail(llm_span or root, exc)
            yield sse("error", {"message": "Something went wrong. Please try again."})
        finally:
            charge = spent + (streaming_est + _est_tokens(partial_chars) if streaming_est else 0)
            if quota is not None and charge:
                await quota.add(user_id, charge)
            metrics.CHAT_REQUESTS.labels(outcome).inc()
            if outcome in ("ok", "cached", "not_found"):
                metrics.CHAT_TOTAL.observe(time.perf_counter() - t0)
            if llm_span is not None:
                llm_span.end()
            root.set_attribute("chat.outcome", outcome)
            root.set_attribute("chat.tokens", charge)
            root.end()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
