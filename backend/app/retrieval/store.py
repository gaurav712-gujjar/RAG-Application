import asyncio
import uuid
from dataclasses import dataclass

import numpy as np
from qdrant_client import AsyncQdrantClient, models

from app.chunking.splitter import Chunk
from app.retrieval.fusion import rrf
from app.retrieval.sparse import document_vector, query_vector

_POINT_NS = uuid.UUID("6f1c5c7e-1d0e-4b5e-9d57-2f5d1f0a9b11")
_UPSERT_BATCH = 64
DENSE = "dense"
SPARSE = "bm25"


@dataclass
class Hit:
    score: float  # the relevance used for thresholding: rerank score if reranked, else dense cosine
    text: str
    filename: str
    page: int
    doc_id: str
    chunk_index: int
    dense_score: float = 0.0
    rerank_score: float | None = None
    sparse_score: float = 0.0  # BM25 score from the keyword leg (0 if it did not match)
    sparse_rank: int | None = None  # 0 = best keyword match


def user_filter(user_id: str, doc_id: str | None = None) -> models.Filter:
    """Tenant isolation. Every read and delete goes through this; user_id is mandatory."""
    if not user_id:
        raise ValueError("user_id is required")
    must = [models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))]
    if doc_id:
        must.append(models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)))
    return models.Filter(must=must)


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb / denom) if denom else 0.0


def make_client(url: str, api_key: str = "") -> AsyncQdrantClient:
    """":memory:" gives an in-process Qdrant (tests, eval). Otherwise connect to a server."""
    if url == ":memory:":
        return AsyncQdrantClient(location=":memory:")
    return AsyncQdrantClient(url=url, api_key=api_key or None)


class VectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str, dim: int):
        self.client = client
        self.collection = collection
        self.dim = dim

    async def ensure_collection(self) -> None:
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                self.collection,
                vectors_config={DENSE: models.VectorParams(size=self.dim, distance=models.Distance.COSINE)},
                # IDF modifier: Qdrant computes inverse document frequency server-side (BM25)
                sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
        for field in ("user_id", "doc_id"):
            await self.client.create_payload_index(
                self.collection, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
            )

    async def upsert_chunks(
        self,
        *,
        user_id: str,
        doc_id: str,
        filename: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")
        points = [
            models.PointStruct(
                # deterministic id -> re-ingesting the same doc overwrites, never duplicates
                id=str(uuid.uuid5(_POINT_NS, f"{doc_id}:{c.index}")),
                vector={DENSE: v, SPARSE: document_vector(c.text)},
                payload={
                    "user_id": user_id,
                    "doc_id": doc_id,
                    "filename": filename,
                    "page": c.page,
                    "chunk_index": c.index,
                    "text": c.text,
                },
            )
            for c, v in zip(chunks, vectors)
        ]
        for i in range(0, len(points), _UPSERT_BATCH):
            await self.client.upsert(self.collection, points=points[i : i + _UPSERT_BATCH])

    @staticmethod
    def _hit(point, dense_score: float) -> Hit:
        p = point.payload
        return Hit(
            score=dense_score,
            dense_score=dense_score,
            text=p["text"],
            filename=p["filename"],
            page=p["page"],
            doc_id=p["doc_id"],
            chunk_index=p["chunk_index"],
        )

    async def search(self, user_id: str, vector: list[float], top_k: int) -> list[Hit]:
        """Dense-only search (used when hybrid is disabled)."""
        res = await self.client.query_points(
            self.collection,
            query=vector,
            using=DENSE,
            query_filter=user_filter(user_id),
            limit=top_k,
            with_payload=True,
        )
        return [self._hit(p, p.score) for p in res.points]

    async def hybrid_search(
        self, user_id: str, vector: list[float], query_text: str, top_k: int
    ) -> list[Hit]:
        """Dense + BM25, fused with RRF. Both legs carry the user_id filter.

        Fusion is done client-side so each hit keeps a real dense cosine (Qdrant's own RRF only
        returns fused rank scores, which can't be thresholded)."""
        flt = user_filter(user_id)
        common = dict(query_filter=flt, limit=top_k, with_payload=True, with_vectors=[DENSE])
        legs = [self.client.query_points(self.collection, query=vector, using=DENSE, **common)]
        sparse_q = query_vector(query_text)
        if sparse_q.indices:  # a query made only of stopwords has no keyword leg
            legs.append(self.client.query_points(self.collection, query=sparse_q, using=SPARSE, **common))
        results = await asyncio.gather(*legs)
        keyword = {}  # point id -> (rank, bm25 score) from the keyword leg
        if sparse_q.indices:
            keyword = {str(p.id): (i, p.score) for i, p in enumerate(results[1].points)}

        points = {}
        rankings = []
        for res in results:
            rankings.append([str(p.id) for p in res.points])
            for p in res.points:
                points.setdefault(str(p.id), p)
        fused = rrf(rankings)
        ordered = sorted(fused, key=fused.get, reverse=True)[:top_k]
        hits = []
        for pid in ordered:
            hit = self._hit(points[pid], _cosine(vector, points[pid].vector[DENSE]))
            if pid in keyword:
                hit.sparse_rank, hit.sparse_score = keyword[pid][0], float(keyword[pid][1])
            hits.append(hit)
        return hits

    async def delete_document(self, user_id: str, doc_id: str) -> None:
        await self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(filter=user_filter(user_id, doc_id)),
        )