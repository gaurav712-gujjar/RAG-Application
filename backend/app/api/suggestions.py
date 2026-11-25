"""Starter questions for a document. One small LLM call the first time, then stored."""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_session
from app.llm.prompts import answer_language
from app.models import DocStatus, DocSuggestion, Document, User

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/documents", tags=["documents"])

_SYSTEM = (
    "Write 3 short, specific questions a person could ask about this document excerpt. "
    "One per line, no numbering, no extra text. Write them in {lang}."
)


class SuggestionsOut(BaseModel):
    questions: list[str]


def parse_questions(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        q = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip('"')
        if 8 <= len(q) <= 160 and q not in out:
            out.append(q)
    return out[:3]


@router.get("/{doc_id}/suggestions", response_model=SuggestionsOut)
async def suggestions(
    doc_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    doc = await session.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(404, "Document not found")
    if doc.status != DocStatus.READY:
        return SuggestionsOut(questions=[])
    saved = await session.get(DocSuggestion, doc_id)
    if saved is not None:
        return SuggestionsOut(questions=saved.questions)

    state = request.app.state
    chunks = await state.store.first_chunks(user.id, doc_id, 3)
    if not chunks:
        return SuggestionsOut(questions=[])
    excerpt = "\n".join(chunks)[:1800]
    try:
        res = await state.llm.complete(
            [
                {"role": "system", "content": _SYSTEM.format(lang=answer_language(excerpt[:300]))},
                {"role": "user", "content": excerpt},
            ],
            settings.llm_model_fast, 200, 0.3,
        )
    except Exception:
        logger.warning("suggestions failed", exc_info=True)
        return SuggestionsOut(questions=[])  # nothing is saved, so the next visit tries again
    questions = parse_questions(res.text)
    quota = getattr(state, "quota", None)
    spent = (res.prompt_tokens or 0) + (res.completion_tokens or 0)
    if quota is not None and spent:
        await quota.add(user.id, spent)
    if questions:
        session.add(DocSuggestion(doc_id=doc_id, user_id=user.id, questions=questions))
        await session.commit()
    return SuggestionsOut(questions=questions)
