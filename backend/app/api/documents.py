import asyncio
import datetime as dt
import hashlib
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.core.config import get_settings
from app.core.db import SessionLocal, get_session
from app.core.limits import limit_upload
from app.models import DocStatus, Document, User
from app.parsers.files import UnsupportedFile, detect_file_type, sanitize_filename
from app.workers.dispatch import enqueue_ingestion
from app.workers.pipeline import process_document

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/documents", tags=["documents"])


class DocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    size_bytes: int
    status: str
    error: str | None
    num_chunks: int
    created_at: dt.datetime


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):
        buf += chunk
        if len(buf) > max_bytes:
            raise HTTPException(413, f"File too large (max {settings.max_upload_mb} MB)")
    return bytes(buf)


async def _find_by_hash(session: AsyncSession, user_id: str, file_hash: str) -> Document | None:
    return (
        await session.execute(
            select(Document).where(Document.user_id == user_id, Document.file_hash == file_hash)
        )
    ).scalar_one_or_none()


@router.post(
    "", response_model=DocOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit_upload)]
)
async def upload_document(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Validate + store the file, then ingest.
    - ingest_mode=celery: returns 202 with status "uploaded"; a worker does the rest.
    - ingest_mode=inline: processes before returning 201 (dev/tests)."""
    state = request.app.state
    data = await _read_limited(file, settings.max_upload_mb * 1024 * 1024)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    filename = sanitize_filename(file.filename)
    try:
        ftype = detect_file_type(filename, data)
    except UnsupportedFile as e:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e))

    # Idempotency: same bytes uploaded again by the same user -> return the existing doc.
    file_hash = hashlib.sha256(data).hexdigest()
    existing = await _find_by_hash(session, user.id, file_hash)
    if existing:
        if existing.status != DocStatus.FAILED:
            response.status_code = status.HTTP_200_OK
            return existing
        await session.delete(existing)  # re-upload of a failed doc = retry
        await session.commit()

    doc_id = str(uuid.uuid4())
    key = f"{user.id}/{doc_id}.{ftype}"
    await asyncio.to_thread(state.storage.save, key, data)

    doc = Document(
        id=doc_id, user_id=user.id, filename=filename, file_type=ftype, file_hash=file_hash,
        size_bytes=len(data), status=DocStatus.UPLOADED, storage_path=key,
    )
    session.add(doc)
    try:
        await session.commit()
    except IntegrityError:  # concurrent duplicate upload won the race
        await session.rollback()
        await asyncio.to_thread(state.storage.delete, key)
        response.status_code = status.HTTP_200_OK
        return await _find_by_hash(session, user.id, file_hash)

    if settings.ingest_mode == "celery":
        try:
            await asyncio.to_thread(enqueue_ingestion, doc_id)
        except Exception:
            logger.exception("Could not enqueue doc %s", doc_id)
            doc.status, doc.error = DocStatus.FAILED, "Could not queue the document. Please retry."
            await session.commit()
            await asyncio.to_thread(state.storage.delete, key)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Processing queue unavailable")
        response.status_code = status.HTTP_202_ACCEPTED
        return doc

    await process_document(
        doc_id,
        session_factory=SessionLocal,
        storage=state.storage,
        embedder=state.embedder,
        store=state.store,
        settings=settings,
        cache=getattr(state, "cache", None),
    )
    await session.refresh(doc)
    return doc


@router.get("", response_model=list[DocOut])
async def list_documents(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())
    )
    return rows.scalars().all()


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Cascade: vectors -> stored file -> DB row. Vectors go first so a failure leaves the
    document intact and the delete can simply be retried."""
    doc = (
        await session.execute(
            select(Document).where(Document.id == doc_id, Document.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await request.app.state.store.delete_document(user.id, doc.id)
    await asyncio.to_thread(request.app.state.storage.delete, doc.storage_path)
    await session.delete(doc)
    await session.commit()
    cache = getattr(request.app.state, "cache", None)
    if cache is not None:
        await cache.invalidate_user(user.id)  # cached answers may quote the deleted document
    return Response(status_code=status.HTTP_204_NO_CONTENT)
