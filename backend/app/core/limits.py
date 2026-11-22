"""Per-user token quota and request rate limiting, both backed by Redis (async).

Both fail open: if Redis is unreachable we log once a minute and let the request through, so a
Redis outage degrades cost protection instead of taking the whole app down.
"""
import datetime as dt
import math
import time
import uuid

from fastapi import Depends, HTTPException, Request

from app.auth.deps import get_current_user
from app.core.cache import redis_down, warn_redis
from app.core.config import get_settings
from app.models import User


class QuotaExceeded(Exception):
    def __init__(self, used: int, limit: int, retry_after: int):
        self.used, self.limit, self.retry_after = used, limit, retry_after


class TokenQuota:
    """Daily token budget per user (resets at 00:00 UTC). Soft limit: a request that starts under
    the limit is allowed to finish, so a user can overshoot by at most one answer."""

    def __init__(self, redis, daily_limit: int):
        self.redis = redis
        self.daily_limit = daily_limit

    @staticmethod
    def _today() -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")

    def _key(self, user_id: str) -> str:
        return f"quota:{user_id}:{self._today()}"

    @staticmethod
    def seconds_until_reset() -> int:
        now = dt.datetime.now(dt.timezone.utc)
        midnight = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, int((midnight - now).total_seconds()))

    async def used(self, user_id: str) -> int:
        if redis_down():
            return 0
        try:
            return int(await self.redis.get(self._key(user_id)) or 0)
        except Exception as e:
            warn_redis("quota read", e)
            return 0

    async def check(self, user_id: str) -> None:
        if self.daily_limit <= 0:
            return
        used = await self.used(user_id)
        if used >= self.daily_limit:
            raise QuotaExceeded(used, self.daily_limit, self.seconds_until_reset())

    async def add(self, user_id: str, tokens: int) -> None:
        if tokens <= 0 or redis_down():
            return
        try:
            key = self._key(user_id)
            await self.redis.incrby(key, int(tokens))
            await self.redis.expire(key, 2 * 24 * 3600)
        except Exception as e:
            warn_redis("quota write", e)


class RateLimiter:
    """Sliding-window log: exact (no 2x burst at window edges like fixed windows)."""

    def __init__(self, redis):
        self.redis = redis

    async def hit(self, key: str, limit: int, window_s: int) -> int | None:
        """Record one request. Returns None if allowed, else seconds to wait (Retry-After)."""
        if limit <= 0 or redis_down():
            return None
        try:
            now = time.time()
            member = f"{now}:{uuid.uuid4().hex[:8]}"
            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(key, 0, now - window_s)
            pipe.zadd(key, {member: now})
            pipe.zcard(key)
            pipe.expire(key, window_s + 1)
            _, _, count, _ = await pipe.execute()
            if count <= limit:
                return None
            await self.redis.zrem(key, member)  # rejected requests don't extend the window
            oldest = await self.redis.zrange(key, 0, 0, withscores=True)
            wait = (oldest[0][1] + window_s - now) if oldest else window_s
            return max(1, math.ceil(wait))
        except Exception as e:
            warn_redis("rate limit", e)
            return None


def _too_many(retry_after: int, what: str) -> HTTPException:
    return HTTPException(
        429, f"Too many {what}. Try again in {retry_after}s.", headers={"Retry-After": str(retry_after)}
    )


async def _enforce(request: Request, key: str, limit: int, what: str) -> None:
    limiter: RateLimiter | None = getattr(request.app.state, "limiter", None)
    if limiter is None:
        return
    retry_after = await limiter.hit(key, limit, 60)
    if retry_after:
        raise _too_many(retry_after, what)


# --- FastAPI dependencies (per user, after authentication) ---
async def limit_chat(request: Request, user: User = Depends(get_current_user)) -> None:
    await _enforce(request, f"rl:chat:{user.id}", get_settings().rate_limit_chat_per_min, "chat requests")


async def limit_upload(request: Request, user: User = Depends(get_current_user)) -> None:
    await _enforce(
        request, f"rl:upload:{user.id}", get_settings().rate_limit_upload_per_min, "uploads"
    )


# --- per client IP, for unauthenticated auth endpoints (brute-force protection) ---
async def limit_auth(request: Request) -> None:
    # Behind a reverse proxy, run uvicorn with --proxy-headers so this is the real client IP.
    ip = request.client.host if request.client else "unknown"
    await _enforce(request, f"rl:auth:{ip}", get_settings().rate_limit_auth_per_min, "login attempts")