import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


class FastEmbedEmbedder:
    """Local ONNX embeddings (no torch). Same interface as the other embedders."""

    def __init__(self, model_name: str, dim: int, batch_size: int = 16):
        self.model_name = model_name
        self.dim = dim
        self.batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        texts = [t if t.strip() else " " for t in texts]
        return [v.tolist() for v in self._load().embed(texts, batch_size=self.batch_size)]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, texts)

    async def warmup(self) -> None:
        vec = await asyncio.to_thread(self._encode, ["warm up"])
        if len(vec[0]) != self.dim:
            raise RuntimeError(f"Model gives {len(vec[0])}-dim vectors but EMBEDDING_DIM={self.dim}")
        logger.info("FastEmbed ready (model=%s, dim=%d)", self.model_name, self.dim)