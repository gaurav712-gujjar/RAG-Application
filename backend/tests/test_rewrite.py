from app.core.config import get_settings
from app.llm.rewrite import needs_rewrite
from app.llm.prompts import NOT_FOUND
from app.main import app
from tests.helpers import parse_sse, signup, upload

settings = get_settings()
DOC = b"Refunds are processed within 14 days. The refund policy covers all purchases."
FIRST = {"role": "user", "content": "What is the refund policy?"}
ANSWER = {"role": "assistant", "content": "Refunds take 14 days [1]."}


def test_needs_rewrite_only_for_follow_ups():
    hist = [FIRST, ANSWER]
    assert not needs_rewrite("anything at all", [])  # no history: never
    assert needs_rewrite("and how long?", hist)  # short
    assert needs_rewrite("Can you explain more about what it covers in detail please", hist)  # pronoun
    assert needs_rewrite("iska time kitna lagta hai", hist)  # Hinglish pronoun
    assert not needs_rewrite("What are the shipping costs for international orders to Canada", hist)


async def _ready(ctx, email="r@example.com"):
    h = await signup(ctx, email)
    await upload(ctx, h, "policy.txt", DOC)
    return h


async def test_follow_up_is_rewritten_before_retrieval(ctx):
    h = await _ready(ctx)
    llm = app.state.llm
    llm.rewrite_result = "refund policy 14 days"
    # on its own, "and how long?" shares no words with the document
    r = await ctx.post("/chat", headers=h, json={"question": "and how long?", "history": [FIRST, ANSWER]})
    events = dict(parse_sse(r.text))
    assert llm.complete_calls == 1
    assert events["sources"], "rewritten query should have found the document"
    assert events["token"]["t"] != NOT_FOUND
    # the rewrite prompt saw the previous turn
    assert "What is the refund policy?" in llm.last_complete_messages[-1]["content"]
    # the final answer prompt still uses the user's own words
    assert "Q: and how long?\nAnswer in English only." in llm.last_messages[-1]["content"]


async def test_no_rewrite_call_without_history(ctx):
    h = await _ready(ctx)
    await ctx.post("/chat", headers=h, json={"question": "What is the refund policy?"})
    assert app.state.llm.complete_calls == 0


async def test_rewrite_failure_falls_back_to_heuristic(ctx):
    h = await _ready(ctx)
    app.state.llm.fail_complete = True
    r = await ctx.post("/chat", headers=h, json={"question": "and how long?", "history": [FIRST, ANSWER]})
    events = dict(parse_sse(r.text))
    assert app.state.llm.complete_calls == 1
    assert events["sources"]  # previous user question was prepended to the search query


async def test_rewrite_can_be_disabled(ctx, monkeypatch):
    h = await _ready(ctx)
    monkeypatch.setattr(settings, "rewrite_enabled", False)
    await ctx.post("/chat", headers=h, json={"question": "and how long?", "history": [FIRST, ANSWER]})
    assert app.state.llm.complete_calls == 0


def test_hinglish_standalone_vs_follow_up():
    from app.llm.rewrite import needs_rewrite

    h = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
    assert not needs_rewrite("Free shipping kab milti hai?", h)  # 5 words, stands on its own
    assert needs_rewrite("iska charge kitna hai?", h)
    assert needs_rewrite("और बैटरी पर?", h)
    assert needs_rewrite("and sick leave?", h)
    assert not needs_rewrite("iska charge kitna hai?", [])  # no history: nothing to rewrite


def test_answer_language_follows_the_question_not_the_context():
    from app.llm.prompts import answer_language

    assert answer_language("How many days of earned leave carry forward?") == "English"
    assert answer_language("वारंटी कितने समय की होती है?") == "Hindi"
    assert answer_language("Refund kitne din me aata hai?").startswith("Hinglish")
    assert answer_language("Free shipping kab milti hai?").startswith("Hinglish")
    assert answer_language("Tell me about the refund policy") == "English"  # 'me' alone is English
