"""Chat history kept on the server, so a person sees their chats on any device."""
import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.core.db import get_session
from app.core.demo import is_demo
from app.models import ChatThread, User

router = APIRouter(prefix="/threads", tags=["threads"])

MAX_THREADS = 30
MAX_MESSAGES = 60
MAX_BYTES = 200_000  # per chat


class ThreadIn(BaseModel):
    title: str = Field(default="New chat", max_length=120)
    messages: list[dict] = Field(default_factory=list, max_length=MAX_MESSAGES)


class ThreadOut(BaseModel):
    id: str
    title: str
    updated_at: dt.datetime
    messages: list[dict]


def _out(t: ChatThread) -> ThreadOut:
    return ThreadOut(id=t.id, title=t.title, updated_at=t.updated_at, messages=t.messages or [])


@router.get("", response_model=list[ThreadOut])
async def list_threads(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if is_demo(user):
        return []  # the demo account is shared: its chats are never stored
    rows = await session.execute(
        select(ChatThread).where(ChatThread.user_id == user.id).order_by(ChatThread.updated_at.desc()).limit(MAX_THREADS)
    )
    return [_out(t) for t in rows.scalars()]


@router.put("/{thread_id}", response_model=ThreadOut)
async def save_thread(
    thread_id: str,
    body: ThreadIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if is_demo(user):
        return ThreadOut(id=thread_id, title=body.title, updated_at=dt.datetime.now(dt.timezone.utc), messages=body.messages)
    if not (8 <= len(thread_id) <= 36):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid chat id")
    if len(json.dumps(body.messages, ensure_ascii=False)) > MAX_BYTES:
        raise HTTPException(413, "Chat is too large to save")
    t = await session.get(ChatThread, thread_id)
    if t is not None and t.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")  # never reveal another user's id
    if t is None:
        t = ChatThread(id=thread_id, user_id=user.id)
        session.add(t)
    t.title, t.messages, t.updated_at = body.title, body.messages, dt.datetime.now(dt.timezone.utc)
    await session.commit()
    return _out(t)


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    if is_demo(user):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    await session.execute(delete(ChatThread).where(ChatThread.id == thread_id, ChatThread.user_id == user.id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
