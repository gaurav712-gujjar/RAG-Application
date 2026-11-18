"""A tiny deterministic embedder with no model download (hashed bag of words).

It has no semantic understanding, so it must never be used for real answers. It exists so tests and
the eval harness's `--fast` mode can exercise the whole pipeline in seconds, offline.
"""
import hashlib
import math
import re

_STOP = {"the", "a", "an", "is", "are", "of", "to", "in", "and", "what", "how", "do", "does",
         "for", "on", "it", "this", "that", "i", "my", "can", "within"}


class HashingEmbedder:
    dim = 512

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in re.findall(r"\w+", text.lower()):
            if w in _STOP:
                continue
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:
            v[-1] = 1.0
            return v
        return [x / norm for x in v]
