"""Hosted embeddings via the Gemini API (no local model, so ~no RAM). Needs GEMINI_API_KEY."""
import asyncio
import math

import httpx

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
_BATCH = 100  # API maximum per batch request


class GeminiEmbedder:
    def __init__(self, api_key: str, model: str = "gemini-embedding-001", dim: int = 768,
                 client: httpx.AsyncClient | None = None, max_retries: int = 5):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is empty")
        self.api_key = api_key
        self.model = model.removeprefix("models/")
        self.dim = dim
        self.max_retries = max_retries
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
        for attempt in range(self.max_retries + 1):
            r = await self._client.post(url, json=body, headers={"x-goog-api-key": self.api_key})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                retry_after = r.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 20)
                await asyncio.sleep(delay)
                continue
            r.raise_for_status()
            return [e["values"] for e in r.json()["embeddings"]]
        raise RuntimeError("unreachable")

    @staticmethod
    def _normalize(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]  # truncated dims are not unit length

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            out.extend(self._normalize(v) for v in await self._post(texts[i : i + _BATCH]))
        return out

    async def warmup(self) -> None:
        await self.embed(["warm up"])