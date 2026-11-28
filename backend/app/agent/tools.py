"""Tools the agent can call. Each one returns plain text for the model and may add numbered sources.

Sources are numbered across the whole run, so a citation like [2] means the same thing no matter which
tool call produced it."""
import ast
import json
import operator
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.config import Settings
from app.core.db import SessionLocal
from app.models import DocStatus, Document
from app.retrieval.search import retrieve
from app.websearch.search import web_search

MAX_TOOL_CHARS = 2400  # keeps each tool result (and so the prompt) small

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "search_documents",
        "description": "Search the user's uploaded documents. Use a short, specific query; call it again for a different part of the question.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "search_web",
        "description": "Search the public web. Only for current or public information the documents cannot have.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression exactly, e.g. '4200 * 0.10' or '(999 + 49) * 3'. Use it for ALL arithmetic.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
    }},
    {"type": "function", "function": {
        "name": "list_documents",
        "description": "List the names of the user's ready documents.",
        "parameters": {"type": "object", "properties": {}},
    }},
]

LABELS = {
    "search_documents": "Searched documents",
    "search_web": "Searched the web",
    "calculator": "Calculated",
    "list_documents": "Listed documents",
}

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.FloorDiv: operator.floordiv,
}


def safe_eval(expr: str) -> float:
    """Arithmetic only: numbers, + - * / // % ** and parentheses. Anything else is rejected."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            left, right = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 100:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -ev(n.operand) if isinstance(n.op, ast.USub) else ev(n.operand)
        raise ValueError("only arithmetic is allowed")

    cleaned = expr.replace(",", "").replace("×", "*").replace("÷", "/").replace("^", "**")
    if len(cleaned) > 200:
        raise ValueError("expression too long")
    return ev(ast.parse(cleaned.strip(), mode="eval"))


@dataclass
class AgentToolbox:
    user_id: str
    state: object  # app.state: embedder, store, reranker
    settings: Settings
    doc_ids: list[str] | None = None
    sources: list[dict] = field(default_factory=list)
    _seen: dict = field(default_factory=dict)

    def _add_source(self, key: tuple, src: dict) -> int:
        if key in self._seen:
            return self._seen[key]
        n = len(self.sources) + 1
        self._seen[key] = n
        self.sources.append({"n": n, **src})
        return n

    async def run(self, name: str, args: dict) -> str:
        try:
            if name == "search_documents":
                return await self._search_documents(str(args.get("query", "")).strip())
            if name == "search_web":
                return await self._search_web(str(args.get("query", "")).strip())
            if name == "calculator":
                return self._calculator(str(args.get("expression", "")))
            if name == "list_documents":
                return await self._list_documents()
            return f"Unknown tool {name!r}."
        except Exception as exc:  # a tool failing must not end the run: tell the model and let it adapt
            return f"Tool error: {exc}"

    async def _search_documents(self, query: str) -> str:
        if not query:
            return "Empty query."
        r = await retrieve(
            self.state.embedder, self.state.store, getattr(self.state, "reranker", None),
            self.user_id, query, self.settings, doc_ids=self.doc_ids,
        )
        if not r.hits:
            return "No relevant passages found in the documents."
        out = []
        for h in r.hits:
            n = self._add_source(
                ("doc", h.doc_id, h.page, h.text[:60]),
                {"filename": h.filename, "page": h.page, "doc_id": h.doc_id, "score": round(h.score, 3),
                 "text": h.text[:700]},
            )
            where = f"{h.filename}" + (f", page {h.page}" if h.page else "")
            out.append(f"[{n}] ({where}) {h.text}")
        return "\n\n".join(out)[:MAX_TOOL_CHARS]

    async def _search_web(self, query: str) -> str:
        if not query:
            return "Empty query."
        results = await web_search(
            query, max_results=getattr(self.settings, "web_search_results", 5),
            region=getattr(self.settings, "web_search_region", "in-en"),
        )
        if not results:
            return "No web results found."
        out = []
        for w in results:
            n = self._add_source(
                ("web", w.url), {"kind": "web", "filename": w.title, "url": w.url, "page": None, "doc_id": None}
            )
            out.append(f"[{n}] {w.title} ({w.url}) {w.snippet}")
        return "\n\n".join(out)[:MAX_TOOL_CHARS]

    @staticmethod
    def _calculator(expression: str) -> str:
        try:
            value = safe_eval(expression)
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
            return f"Cannot calculate: {exc}"
        return f"{expression} = {round(value, 6):g}" if isinstance(value, float) else f"{expression} = {value}"

    async def _list_documents(self) -> str:
        async with SessionLocal() as session:
            rows = await session.execute(
                select(Document.filename).where(Document.user_id == self.user_id, Document.status == DocStatus.READY)
                .order_by(Document.created_at)
            )
            names = [r[0] for r in rows]
        return json.dumps(names, ensure_ascii=False) if names else "The user has no ready documents."
