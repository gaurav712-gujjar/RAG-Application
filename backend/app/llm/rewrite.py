"""Turn a follow-up ("and how long does it take?") into a standalone search query.

Only runs for follow-ups: no history means no call, and a question that already stands on its own
doesn't need one either, so most requests pay no extra tokens."""
import logging
import re

from app.core.config import Settings
from app.llm.groq_client import LLM

logger = logging.getLogger(__name__)

_REFERS_BACK = re.compile(
    r"\b(it|its|this|that|these|those|they|them|their|he|she|his|her|above|previous|same|also|"
    r"iska|uska|isme|usme|yeh|ye|woh|wo|isse|usse)\b",
    re.IGNORECASE,
)

SYSTEM = (
    "Rewrite the last question as one standalone search query, using the chat for context. "
    "Keep names, numbers and the language. Output only the query."
)


def needs_rewrite(question: str, history: list[dict]) -> bool:
    if not history:
        return False
    return len(question.split()) <= 6 or bool(_REFERS_BACK.search(question))


async def rewrite_query(
    llm: LLM, settings: Settings, question: str, history: list[dict]
) -> tuple[str | None, int]:
    """Returns (query, tokens_used). query is None when the rewrite failed or came back empty,
    so the caller keeps its own fallback instead of losing the conversation context."""
    prev_q = next((t["content"] for t in reversed(history) if t["role"] == "user"), "")
    prev_a = next((t["content"] for t in reversed(history) if t["role"] == "assistant"), "")
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"Previous question: {prev_q[:300]}\nPrevious answer: {prev_a[:200]}\n"
            f"Last question: {question}",
        },
    ]
    try:
        res = await llm.complete(messages, settings.llm_model_fast, 48, 0.0)
    except Exception:
        logger.warning("Query rewrite failed; falling back to the heuristic query", exc_info=True)
        return None, 0
    tokens = (res.prompt_tokens or 0) + (res.completion_tokens or 0)
    return (res.text.strip().strip('"') or None), tokens
