"""Answer cache in Redis. Fails open: a Redis outage means cache misses, never errors."""
import hashlib
import json
import logging
import re
import time

from app.core import metrics

logger = logging.getLogger(__name__)
_last_warn = 0.0


def warn_redis(what: str, exc: Exception) -> None:
    """Log Redis trouble at most once a minute (it would otherwise log on every request)."""
    global _last_warn
    metrics.REDIS_UNAVAILABLE.labels(what).inc()  # every failure is counted, even when the log is throttled
    if time.monotonic() - _last_warn > 60:
        _last_warn = time.monotonic()
        logger.warning("Redis unavailable for %s (%s); continuing without it", what, type(exc).__name__)


def _normalize(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower()).rstrip("?!. ")


class AnswerCache:
    """Key = user + that user's document version + normalised question.

    * Per-user: one user can never be served another user's answer.
    * Versioned: uploading/deleting a document bumps the user's version, which instantly orphans
      every cached answer that might now be stale (old keys just expire via TTL).
    """

    def __init__(self, redis, ttl_s: int):
        self.redis = redis
        self.ttl_s = ttl_s

    async def _version(self, user_id: str) -> str:
        return await self.redis.get(f"docver:{user_id}") or "0"

    def _key(self, user_id: str, version: str, question: str) -> str:
        digest = hashlib.sha256(_normalize(question).encode()).hexdigest()[:32]
        return f"ans:{user_id}:{version}:{digest}"

    async def get(self, user_id: str, question: str) -> dict | None:
        try:
            raw = await self.redis.get(self._key(user_id, await self._version(user_id), question))
            return json.loads(raw) if raw else None
        except Exception as e:
            warn_redis("cache get", e)
            return None

    async def set(self, user_id: str, question: str, value: dict) -> None:
        try:
            key = self._key(user_id, await self._version(user_id), question)
            await self.redis.set(key, json.dumps(value, ensure_ascii=False), ex=self.ttl_s)
        except Exception as e:
            warn_redis("cache set", e)

    async def invalidate_user(self, user_id: str) -> None:
        try:
            await self.redis.incr(f"docver:{user_id}")
        except Exception as e:
            warn_redis("cache invalidate", e)
