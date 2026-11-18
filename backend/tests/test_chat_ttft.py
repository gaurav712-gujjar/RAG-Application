from app.llm.prompts import NOT_FOUND
from app.main import app
from tests.helpers import parse_sse, signup, upload

DOC = b"Refunds are processed within 14 days. The refund policy covers all purchases."


async def _setup(ctx):
    headers = await signup(ctx, "carol@example.com")
    await upload(ctx, headers, "policy.txt", DOC)
    return headers


async def test_ttft_event_comes_before_first_token_and_is_measured(ctx):
    headers = await _setup(ctx)
    app.state.llm.delay = 0.15  # simulated model latency before the first token

    r = await ctx.post("/chat", headers=headers, json={"question": "What is the refund policy?"})
    events = parse_sse(r.text)
    names = [e for e, _ in events]

    assert names[0] == "sources"
    assert names.index("ttft") < names.index("token")  # TTFT is its own event, sent first
    assert names[-1] == "done"

    ttft = dict(events)["ttft"]
    done = dict(events)["done"]
    assert ttft["ttft_ms"] >= 150  # includes the 150 ms model delay
    assert ttft["retrieval_ms"] >= 0
    assert done["ttft_ms"] == ttft["ttft_ms"]
    assert done["total_ms"] >= done["ttft_ms"]
    assert done["prompt_tokens"] == 120 and done["completion_tokens"] == 8


async def test_empty_delta_does_not_count_as_first_token(ctx):
    headers = await _setup(ctx)
    r = await ctx.post("/chat", headers=headers, json={"question": "What is the refund policy?"})
    tokens = [d["t"] for e, d in parse_sse(r.text) if e == "token"]
    assert "" not in tokens
    assert "".join(tokens) == "Refunds take 14 days [1]."


async def test_not_found_skips_llm(ctx):
    headers = await _setup(ctx)
    app.state.llm.calls = 0
    r = await ctx.post("/chat", headers=headers, json={"question": "quantum chromodynamics lattice gauge"})
    events = dict(parse_sse(r.text))
    assert events["token"]["t"] == NOT_FOUND
    assert events["done"]["model"] is None
    assert app.state.llm.calls == 0


async def test_prompt_is_compact_and_cited(ctx):
    headers = await _setup(ctx)
    await ctx.post("/chat", headers=headers, json={"question": "What is the refund policy?"})
    msgs = app.state.llm.last_messages
    assert msgs[0]["role"] == "system" and "Not found in documents" in msgs[0]["content"]
    assert "[1] policy.txt p1:" in msgs[-1]["content"]


async def test_history_is_trimmed_to_last_two_turns(ctx):
    headers = await _setup(ctx)
    history = []
    for i in range(5):
        history += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    await ctx.post("/chat", headers=headers, json={"question": "What is the refund policy?", "history": history})
    msgs = app.state.llm.last_messages
    assert len(msgs) == 1 + 4 + 1  # system + 2 turns (4 msgs) + question
    assert msgs[1]["content"] == "q3"


async def test_model_routing(ctx):
    headers = await _setup(ctx)
    await ctx.post("/chat", headers=headers, json={"question": "What is the refund policy?"})
    assert app.state.llm.last_model == "llama-3.1-8b-instant"
    await ctx.post("/chat", headers=headers, json={"question": "Summarize the refund policy"})
    assert app.state.llm.last_model == "llama-3.3-70b-versatile"
