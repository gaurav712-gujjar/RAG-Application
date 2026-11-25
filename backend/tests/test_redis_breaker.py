"""When Redis is down we must not wait for it on every request."""
import time

from app.core import cache as cache_mod
from app.core.cache import AnswerCache, redis_down, reset_breaker
from app.core.limits import RateLimiter, TokenQuota


class DeadRedis:
    calls = 0

    def __getattr__(self, name):
        async def boom(*a, **k):
            DeadRedis.calls += 1
            time.sleep(0.05)  # a slow failing connection
            raise ConnectionError("refused")

        return boom

    def pipeline(self):
        DeadRedis.calls += 1
        raise ConnectionError("refused")


async def test_after_one_failure_redis_is_skipped_until_the_breaker_closes():
    reset_breaker()
    DeadRedis.calls = 0
    r = DeadRedis()
    cache, quota, limiter = AnswerCache(r, 60), TokenQuota(r, 100), RateLimiter(r)

    assert await cache.get("u", "q") is None  # first call hits Redis, fails, opens the breaker
    assert redis_down() and DeadRedis.calls == 1

    t = time.perf_counter()
    assert await cache.get("u", "q") is None
    await cache.set("u", "q", {})
    await cache.invalidate_user("u")
    await quota.check("u")
    await quota.add("u", 5)
    assert await limiter.hit("k", 5, 60) is None
    assert DeadRedis.calls == 1  # nothing else touched Redis
    assert time.perf_counter() - t < 0.05

    cache_mod._down_until = time.monotonic() - 1  # breaker window over: Redis is tried again
    await cache.get("u", "q")
    assert DeadRedis.calls == 2
    reset_breaker()
