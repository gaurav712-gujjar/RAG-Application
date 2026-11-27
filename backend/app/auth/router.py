import asyncio
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.core.config import get_settings
from app.core.db import SessionLocal, get_session
from app.core.demo import get_or_create_demo_user, seed_demo_documents
from app.core.limits import limit_auth
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: EmailStr


@router.post(
    "/register",
    response_model=TokenOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_auth)],
)
async def register(body: Credentials, session: AsyncSession = Depends(get_session)):
    email = body.email.lower()
    exists = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=email, password_hash=await asyncio.to_thread(hash_password, body.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    return TokenOut(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenOut, dependencies=[Depends(limit_auth)])
async def login(body: Credentials, session: AsyncSession = Depends(get_session)):
    user = (
        await session.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    if not user or not await asyncio.to_thread(verify_password, body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return UserOut(id=user.id, email=user.email)


@router.post("/demo", response_model=TokenOut, dependencies=[Depends(limit_auth)])
async def demo(request: Request, session: AsyncSession = Depends(get_session)):
    """Sign in as the shared read-only demo account (sample documents are indexed in the background)."""
    settings = get_settings()
    if not settings.demo_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Demo is not enabled")
    user = await get_or_create_demo_user(session)
    task = asyncio.create_task(
        seed_demo_documents(user.id, session_factory=SessionLocal, state=request.app.state, settings=settings)
    )
    request.app.state.demo_task = task  # keep a reference so it is not garbage-collected mid-run
    return TokenOut(access_token=create_access_token(user.id))
