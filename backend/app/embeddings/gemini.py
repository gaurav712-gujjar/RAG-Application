"""Hosted embeddings via the Gemini API (no local model, so ~no RAM). Needs GEMINI_API_KEY."""
import asyncio
import collections
import logging
import math
import re
import time

import httpx

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
logger = logging.getLogger(__name__)

# The API accepts up to 100 texts per request, but small batches sent at the same time finish sooner than
# a few big ones sent one after another. At most `concurrency` requests are in flight, which also keeps
# us under the free tier's requests-per-minute limit.
_BATCH = 25
_CONCURRENCY = 4
_PER_MINUTE = 90  # texts per minute we allow ourselves; the free tier counts every text, not every request


# Thin wrappers so tests can run on a fake clock.
def _now() -> float:
    return time.monotonic()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _retry_delay(r: httpx.Response, attempt: int) -> float:
    """How long to wait before retrying: Retry-After, else the retryDelay Gemini puts in the error body,
    else a growing default (rate-limit windows are measured in tens of seconds, so start higher for 429)."""
    ra = r.headers.get("retry-after")
    if ra and ra.isdigit():
        return float(ra)
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', r.text or "")
    if m:
        return min(float(m.group(1)) + 1.0, 70.0)
    return min(5.0 * (attempt + 1), 30.0) if r.status_code == 429 else float(min(2**attempt, 20))


class GeminiEmbedder:
    def __init__(self, api_key: str, model: str = "gemini-embedding-001", dim: int = 768,
                 client: httpx.AsyncClient | None = None, max_retries: int = 6,
                 batch_size: int = _BATCH, concurrency: int = _CONCURRENCY, per_minute: int = _PER_MINUTE):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is empty")
        self.api_key = api_key
        self.model = model.removeprefix("models/")
        self.dim = dim
        self.max_retries = max_retries
        self.batch_size = max(1, min(100, batch_size))
        self.concurrency = max(1, concurrency)
        self.per_minute = per_minute  # 0 = no pacing
        self._sent: collections.deque[tuple[float, int]] = collections.deque()
        self._pace_lock = asyncio.Lock()
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def _pace(self, n: int) -> None:
        """Wait until sending `n` more texts keeps us within `per_minute` over any 60 s window."""
        if self.per_minute <= 0:
            return
        n = min(n, self.per_minute)
        async with self._pace_lock:
            while True:
                now = _now()
                while self._sent and now - self._sent[0][0] >= 60.0:
                    self._sent.popleft()
                if sum(c for _, c in self._sent) + n <= self.per_minute:
                    self._sent.append((now, n))
                    return
                wait = self._sent[0][0] + 60.0 - now + 0.05
                logger.info("gemini embed: pacing, waiting %.1fs to stay under %d texts/min", wait, self.per_minute)
                await _sleep(max(wait, 0.05))

    async def _post(self, texts: list[str]) -> list[list[float]]:
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t}]},
                    "outputDimensionality": self.dim,
                }
                for t in texts
            ]
        }
        url = _URL.format(model=self.model)
        await self._pace(len(texts))
        t0 = time.perf_counter()
        for attempt in range(self.max_retries + 1):
            r = await self._client.post(url, json=body, headers={"x-goog-api-key": self.api_key})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                logger.warning("gemini embed: HTTP %s, retry %d", r.status_code, attempt + 1)
                await _sleep(_retry_delay(r, attempt))
                continue
            r.raise_for_status()
            logger.info("gemini embed batch: %d texts in %.2fs (%d retries)", len(texts), time.perf_counter() - t0, attempt)
            return [e["values"] for e in r.json()["embeddings"]]
        raise RuntimeError("unreachable")

    @staticmethod
    def _normalize(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]  # truncated dims are not unit length

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Batches run concurrently (bounded); results keep the input order."""
        batches = [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        gate = asyncio.Semaphore(self.concurrency)

        async def one(batch: list[str]) -> list[list[float]]:
            async with gate:
                return await self._post(batch)

        results = await asyncio.gather(*(one(b) for b in batches))
        return [self._normalize(v) for batch in results for v in batch]

    async def warmup(self) -> None:
        await self.embed(["warm up"])