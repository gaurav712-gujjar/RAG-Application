"""Shared, read-only demo account: lets a visitor try the app without signing up.

The account holds a few sample documents (app/demo/docs). They are stored and indexed once, in the
background, the first time someone asks for the demo. Visitors cannot upload or delete, and their chats
are not saved (they would otherwise be shared between everyone who opens the demo)."""
import asyncio
import hashlib
import logging
import secrets
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.security import hash_password
from app.models import DocStage, DocStatus, Document, User
from app.workers.pipeline import process_document

logger = logging.getLogger(__name__)

DEMO_EMAIL = "demo@docchat.app"
DEMO_DIR = Path(__file__).resolve().parent.parent / "demo" / "docs"
READ_ONLY_MSG = "The demo account is read-only. Create a free account to upload your own documents."
_seed_lock = asyncio.Lock()


def is_demo(user: User) -> bool:
    return user.email == DEMO_EMAIL


def forbid_demo(user: User) -> None:
    if is_demo(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, READ_ONLY_MSG)


async def get_or_create_demo_user(session) -> User:
    user = (await session.execute(select(User).where(User.email == DEMO_EMAIL))).scalar_one_or_none()
    if user is None:  # nobody can sign in with this password; the account is only reachable via /auth/demo
        pw = await asyncio.to_thread(hash_password, secrets.token_urlsafe(32))
        user = User(email=DEMO_EMAIL, password_hash=pw)
        session.add(user)
        await session.commit()
    return user


async def seed_demo_documents(user_id: str, *, session_factory, state, settings) -> None:
    """Store + index each sample file once. Safe to call repeatedly (existing files are skipped)."""
    async with _seed_lock:
        for path in sorted(DEMO_DIR.glob("*.md")):
            data = path.read_bytes()
            file_hash = hashlib.sha256(data).hexdigest()
            async with session_factory() as session:
                existing = (
                    await session.execute(
                        select(Document).where(Document.user_id == user_id, Document.file_hash == file_hash)
                    )
                ).scalar_one_or_none()
                if existing is not None and existing.status == DocStatus.READY:
                    continue
                if existing is not None:
                    await session.delete(existing)  # a half-finished earlier attempt: start over
                    await session.commit()
                doc = Document(
                    user_id=user_id, filename=path.name, file_type="md", file_hash=file_hash,
                    size_bytes=len(data), status=DocStatus.UPLOADED, stage=DocStage.QUEUED, storage_path="",
                )
                session.add(doc)
                await session.flush()
                doc.storage_path = f"{user_id}/{doc.id}.md"
                await asyncio.to_thread(state.storage.save, doc.storage_path, data)
                await session.commit()
                doc_id = doc.id
            try:
                await process_document(
                    doc_id, session_factory=session_factory, storage=state.storage, embedder=state.embedder,
                    store=state.store, settings=settings, cache=getattr(state, "cache", None),
                )
            except Exception:
                logger.exception("Demo document %s failed", path.name)
