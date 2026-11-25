"""Owner-only numbers. Off unless ADMIN_TOKEN is set; sent as the X-Admin-Token header."""
import datetime as dt
import hmac

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Document, User

router = APIRouter(prefix="/admin", tags=["admin"])
settings = get_settings()


@router.get("/usage")
async def admin_usage(request: Request, x_admin_token: str = Header(default="")):
    if not settings.admin_token:
        raise HTTPException(404, "Not found")
    if not hmac.compare_digest(x_admin_token.encode(), settings.admin_token.encode()):
        raise HTTPException(401, "Invalid admin token")

    today = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    tokens: dict[str, int] = {}
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            async for key in redis.scan_iter(match=f"quota:*:{today}", count=200):
                tokens[key.split(":")[1]] = int(await redis.get(key) or 0)
        except Exception:
            tokens = {}  # Redis unreachable: show the rest

    async with SessionLocal() as s:
        users = (await s.execute(select(func.count()).select_from(User))).scalar_one()
        docs = (await s.execute(select(func.count()).select_from(Document))).scalar_one()
        emails = {}
        if tokens:
            rows = await s.execute(select(User.id, User.email).where(User.id.in_(list(tokens))))
            emails = {i: e for i, e in rows}
    return {
        "date": today,
        "users": users,
        "documents": docs,
        "tokens_today": sum(tokens.values()),
        "daily_limit": settings.daily_token_quota,
        "per_user": sorted(
            ({"email": emails.get(uid, uid[:8]), "tokens": n} for uid, n in tokens.items()),
            key=lambda r: -r["tokens"],
        ),
    }
