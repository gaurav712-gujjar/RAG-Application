import json

import pytest

from app.agent.tools import safe_eval
from app.llm.groq_client import ToolCall, ToolTurn
from app.main import app
from tests.helpers import parse_sse, signup, upload

DOC = b"Refunds are processed within 14 days. The restocking fee is 10 percent for opened electronics."


class ScriptedLLM:
    """Plays back a list of turns (or exceptions) for complete_tools and records every call."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    async def complete_tools(self, messages, model, tools, max_tokens, temperature):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


def call(name, **args):
    return ToolCall(id=f"c-{name}", name=name, arguments=json.dumps(args))


def turn(text="", calls=(), p=100, c=20):
    return ToolTurn(text=text, tool_calls=list(calls), prompt_tokens=p, completion_tokens=c)


async def _ask(ctx, monkeypatch, llm, headers, question="Compare and compute", **extra):
    monkeypatch.setattr(app.state, "llm", llm, raising=False)
    r = await ctx.post("/chat", headers=headers, json={"question": question, "mode": "agent", **extra})
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


async def test_agent_uses_documents_and_calculator_then_answers(ctx, monkeypatch):
    h = await signup(ctx, "agent@example.com")
    await upload(ctx, h, "policy.txt", DOC)
    llm = ScriptedLLM([
        turn(calls=[call("search_documents", query="restocking fee"), call("calculator", expression="4200 * 0.10")]),
        turn("The fee is 10 percent, so Rs 420 [1]."),
    ])
    events = await _ask(ctx, monkeypatch, llm, h)
    names = [e for e, _ in events]
    assert names.index("stage") < names.index("tool") < names.index("ttft") < names.index("token") < names.index("done")
    tools = [d for e, d in events if e == "tool"]
    assert [t["name"] for t in tools] == ["search_documents", "calculator"]
    assert tools[0]["detail"] == "restocking fee" and tools[1]["label"] == "Calculated"
    src = [d for e, d in events if e == "sources"][-1]
    assert src and src[0]["filename"] == "policy.txt" and src[0]["n"] == 1
    done = dict(events)["done"]
    assert done["answer_source"] == "agent" and done["agent_steps"] == 2
    assert dict(events)["token"]["t"].startswith("The fee is 10 percent")
    # the calculator result reached the model on the second turn
    tool_msgs = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert any("= 420" in m["content"] for m in tool_msgs)


async def test_agent_tokens_are_charged_to_quota(ctx, monkeypatch):
    h = await signup(ctx, "quota@example.com")
    await upload(ctx, h, "policy.txt", DOC)
    llm = ScriptedLLM([turn(calls=[call("list_documents")], p=200, c=50), turn("You have policy.txt", p=300, c=40)])
    await _ask(ctx, monkeypatch, llm, h)
    usage = (await ctx.get("/usage", headers=h)).json()
    assert usage["used_today"] == 590


async def test_agent_step_limit_forces_a_final_answer_without_tools(ctx, monkeypatch):
    h = await signup(ctx, "limit@example.com")
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "agent_max_steps", 2)
    llm = ScriptedLLM([
        turn(calls=[call("calculator", expression="1+1")]),
        turn(calls=[call("calculator", expression="2+2")]),
        turn("Done: 2 and 4."),
    ])
    events = await _ask(ctx, monkeypatch, llm, h)
    assert len(llm.calls) == 3 and llm.calls[-1]["tools"] is None
    assert dict(events)["token"]["t"] == "Done: 2 and 4."


async def test_agent_survives_a_broken_tool_turn(ctx, monkeypatch):
    h = await signup(ctx, "broken@example.com")
    llm = ScriptedLLM([RuntimeError("tool_use_failed"), turn("Answer without tools.")])
    events = await _ask(ctx, monkeypatch, llm, h)
    assert dict(events)["token"]["t"] == "Answer without tools."
    assert "error" not in [e for e, _ in events]


async def test_agent_does_not_repeat_identical_calls(ctx, monkeypatch):
    h = await signup(ctx, "dup@example.com")
    llm = ScriptedLLM([
        turn(calls=[call("calculator", expression="2+2"), call("calculator", expression="2+2")]),
        turn("4"),
    ])
    events = await _ask(ctx, monkeypatch, llm, h)
    assert len([1 for e, _ in events if e == "tool"]) == 1
    tool_msgs = [m["content"] for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert "already ran" in tool_msgs[1]


async def test_agent_only_sees_the_callers_documents(ctx, monkeypatch):
    a = await signup(ctx, "owner@example.com")
    await upload(ctx, a, "secret.txt", b"The vault code is 4471 and is kept in the blue folder.")
    b = await signup(ctx, "snoop@example.com")
    llm = ScriptedLLM([turn(calls=[call("search_documents", query="vault code")]), turn("nothing")])
    events = await _ask(ctx, monkeypatch, llm, b)
    assert [d for e, d in events if e == "sources"][-1] == []
    tool_msg = [m["content"] for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "4471" not in tool_msg


async def test_agent_web_tool_adds_numbered_web_sources(ctx, monkeypatch):
    from app.websearch.ddg import WebResult

    async def fake(query, **kw):
        return [WebResult("Jaipur", "https://j.test", "Pink City.")]

    monkeypatch.setattr("app.agent.tools.web_search", fake)
    h = await signup(ctx, "web2@example.com")
    llm = ScriptedLLM([turn(calls=[call("search_web", query="Jaipur")]), turn("Pink City [1].")])
    events = await _ask(ctx, monkeypatch, llm, h)
    src = [d for e, d in events if e == "sources"][-1]
    assert src[0]["kind"] == "web" and src[0]["url"] == "https://j.test"


async def test_agent_smalltalk_skips_the_agent(ctx, monkeypatch):
    h = await signup(ctx, "hi@example.com")
    llm = ScriptedLLM([])
    events = await _ask(ctx, monkeypatch, llm, h, question="hi")
    assert llm.calls == [] and dict(events)["done"]["model"] is None


@pytest.mark.parametrize("expr,value", [("4200 * 0.10", 420.0), ("(999 + 49) * 3", 3144), ("2 ** 10", 1024), ("1,200 / 4", 300.0)])
def test_calculator_ok(expr, value):
    assert safe_eval(expr) == value


@pytest.mark.parametrize("expr", ["__import__('os').system('x')", "abs(3)", "2 ** 1000", "a + 1", "[1,2]", "1 if True else 2"])
def test_calculator_rejects_anything_but_arithmetic(expr):
    with pytest.raises((ValueError, SyntaxError)):
        safe_eval(expr)


async def test_agent_mode_is_not_available_to_the_demo_account(ctx):
    from app.main import app

    r = await ctx.post("/auth/demo")
    await app.state.demo_task
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    res = await ctx.post("/chat", json={"question": "compare refund and shipping", "mode": "agent"}, headers=h)
    assert res.status_code == 403 and "demo" in res.json()["detail"]
