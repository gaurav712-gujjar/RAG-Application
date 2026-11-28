"""Hosted embeddings via the Gemini API (no local model, so ~no RAM). Needs GEMINI_API_KEY."""
import asyncio
import logging
import math
import time

import httpx

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
logger = logging.getLogger(__name__)

# The API accepts up to 100 texts per request, but small batches sent at the same time finish sooner than
# a few big ones sent one after another. At most `concurrency` requests are in flight, which also keeps
# us under the free tier's requests-per-minute limit.
_BATCH = 25
_CONCURRENCY = 4


class GeminiEmbedder:
    def __init__(self, api_key: str, model: str = "gemini-embedding-001", dim: int = 768,
                 client: httpx.AsyncClient | None = None, max_retries: int = 5,
                 batch_size: int = _BATCH, concurrency: int = _CONCURRENCY):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is empty")
        self.api_key = api_key
        self.model = model.removeprefix("models/")
        self.dim = dim
        self.max_retries = max_retries
        self.batch_size = max(1, min(100, batch_size))
        self.concurrency = max(1, concurrency)
        self._client = client or httpx.AsyncClient(timeout=30.0)

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
        t0 = time.perf_counter()
        for attempt in range(self.max_retries + 1):
            r = await self._client.post(url, json=body, headers={"x-goog-api-key": self.api_key})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                logger.warning("gemini embed: HTTP %s, retry %d", r.status_code, attempt + 1)
                retry_after = r.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 20)
                await asyncio.sleep(delay)
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