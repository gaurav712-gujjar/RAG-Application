import asyncio
import threading
from typing import Protocol


class Reranker(Protocol):
    async def score(self, query: str, texts: list[str]) -> list[float]:
        """Relevance of each text to the query, 0..1, same order as `texts`."""
        ...


class CrossEncoderReranker:
    """Local cross-encoder (default BAAI/bge-reranker-v2-m3, multilingual). It reads the query and
    the passage together, so it is far more precise than vector similarity, at some latency cost.

    Uses transformers directly (the documented way to run bge-reranker) instead of
    sentence-transformers' CrossEncoder, whose activation arguments differ between major versions.
    The model outputs one logit per pair; sigmoid maps it to 0..1."""

    def __init__(self, model_name: str, max_length: int = 512):
        self.model_name = model_name
        self.max_length = max_length
        self._tokenizer = None
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from transformers import AutoModelForSequenceClassification, AutoTokenizer

                    self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                    self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name).eval()
        return self._tokenizer, self._model

    def _predict(self, query: str, texts: list[str]) -> list[float]:
        import torch

        tokenizer, model = self._load()
        inputs = tokenizer(
            [query] * len(texts),
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        with torch.no_grad():
            logits = model(**inputs).logits.view(-1).float()
        return torch.sigmoid(logits).tolist()

    async def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return await asyncio.to_thread(self._predict, query, texts)

    async def warmup(self) -> None:
        await asyncio.to_thread(self._load)
