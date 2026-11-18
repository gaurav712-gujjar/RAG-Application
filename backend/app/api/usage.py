import datetime as dt

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth.deps import get_current_user
from app.core.limits import TokenQuota
from app.models import User

router = APIRouter(tags=["usage"])


class UsageOut(BaseModel):
    used_today: int
    daily_limit: int  # 0 = unlimited
    resets_at: dt.datetime


@router.get("/usage", response_model=UsageOut)
async def usage(request: Request, user: User = Depends(get_current_user)):
    quota: TokenQuota | None = getattr(request.app.state, "quota", None)
    used = await quota.used(user.id) if quota else 0
    resets_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=TokenQuota.seconds_until_reset())
    return UsageOut(
        used_today=used,
        daily_limit=quota.daily_limit if quota else 0,
        resets_at=resets_at.replace(microsecond=0),
    )
