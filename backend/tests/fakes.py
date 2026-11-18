import asyncio
import hashlib
import io
import math
import re

from app.embeddings.hashing import HashingEmbedder
from app.llm.groq_client import LLMEvent

_STOP = {"the", "a", "an", "is", "are", "of", "to", "in", "and", "what", "how", "do", "does",
         "for", "on", "it", "this", "that", "i", "my", "can", "within"}


FakeEmbedder = HashingEmbedder  # shared with the eval harness's --fast mode


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


class FakeReranker:
    """Score = fraction of the query's content words found in the passage (0..1)."""

    def __init__(self):
        self.calls = 0
        self.fail = False

    async def score(self, query: str, texts: list[str]) -> list[float]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("reranker model crashed")
        q = _words(query)
        if not q:
            return [0.0] * len(texts)
        return [len(q & _words(t)) / len(q) for t in texts]


class FakeLLM:
    def __init__(self, delay: float = 0.05, tokens=("Refunds", " take", " 14 days", " [1].")):
        self.delay = delay
        self.tokens = tokens
        self.calls = 0
        self.last_messages: list[dict] = []
        self.last_model: str | None = None
        # non-streaming calls (follow-up query rewrite)
        self.complete_calls = 0
        self.rewrite_result: str | None = None  # None = echo the last question
        self.fail_complete = False
        self.last_complete_messages: list[dict] = []

    async def complete(self, messages, model, max_tokens, temperature):
        from app.llm.groq_client import LLMCompletion

        self.complete_calls += 1
        self.last_complete_messages = messages
        if self.fail_complete:
            raise RuntimeError("rewrite model down")
        last = messages[-1]["content"].split("Last question:", 1)[-1].strip()
        return LLMCompletion(self.rewrite_result or last, prompt_tokens=30, completion_tokens=5)

    async def stream(self, messages, model, max_tokens, temperature):
        self.calls += 1
        self.last_messages = messages
        self.last_model = model
        await asyncio.sleep(self.delay)
        yield LLMEvent("token", text="")  # empty delta must NOT count as the first token
        for t in self.tokens:
            yield LLMEvent("token", text=t)
            await asyncio.sleep(0.005)
        yield LLMEvent("usage", prompt_tokens=120, completion_tokens=8)


def make_pdf(text: str) -> bytes:
    """Hand-built single-page PDF with extractable text."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_pdf_pages(texts: list[str]) -> bytes:
    """Multi-page PDF. An empty string gives a page with no extractable text (like a scan)."""
    n = len(texts)
    font_id = 3 + 2 * n
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [" + " ".join(f"{3 + 2 * i} 0 R" for i in range(n)) + f"] /Count {n} >>",
    ]
    for i, text in enumerate(texts):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else ""
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        )
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(text: str) -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
