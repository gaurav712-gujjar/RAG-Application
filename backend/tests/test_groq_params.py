"""reasoning_effort is only sent when configured (llama models reject unknown parameters)."""
from app.llm.groq_client import GroqLLM


class _Completions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs

        class _Msg:
            content = "ok"

        class _Choice:
            message = _Msg()

        class _Res:
            choices = [_Choice()]
            usage = None

        return _Res()


def _llm(effort):
    llm = GroqLLM("key", reasoning_effort=effort)
    comp = _Completions()
    llm.client = type("C", (), {"chat": type("Ch", (), {"completions": comp})()})()
    return llm, comp


async def test_reasoning_effort_is_sent_only_when_set():
    llm, comp = _llm("low")
    await llm.complete([{"role": "user", "content": "x"}], "openai/gpt-oss-20b", 10, 0.0)
    assert comp.kwargs["extra_body"] == {"reasoning_effort": "low"}

    llm, comp = _llm("")
    await llm.complete([{"role": "user", "content": "x"}], "llama-3.1-8b-instant", 10, 0.0)
    assert comp.kwargs["extra_body"] is None
