import os
import tempfile

# Must be set BEFORE app modules are imported (settings are read at import time).
_tmp = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite+aiosqlite:///{_tmp}/test.db",
    UPLOAD_DIR=f"{_tmp}/uploads",
    QDRANT_URL=":memory:",
    RELEVANCE_THRESHOLD="0.1",
    MAX_UPLOAD_MB="1",
    JWT_SECRET="test-secret-test-secret-test-secret-123456",
    GROQ_API_KEY="test",
    EMBEDDING_DIM="512",
    # generous defaults so ordinary tests never trip these; specific tests lower them
    RATE_LIMIT_CHAT_PER_MIN="1000",
    RATE_LIMIT_UPLOAD_PER_MIN="1000",
    RATE_LIMIT_AUTH_PER_MIN="1000",
    DAILY_TOKEN_QUOTA="1000000",
)

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402

from tests.telemetry import SPANS  # noqa: E402  (installs the tracer provider before the app is used)
from qdrant_client import AsyncQdrantClient  # noqa: E402

from app.core.db import Base, engine, init_db  # noqa: E402
from fakeredis import FakeAsyncRedis  # noqa: E402

from app.core.cache import AnswerCache  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.limits import RateLimiter, TokenQuota  # noqa: E402
from app.core.storage import get_storage  # noqa: E402
from app.main import app  # noqa: E402
from app.retrieval.store import VectorStore  # noqa: E402
from tests.fakes import FakeEmbedder, FakeLLM, FakeReranker  # noqa: E402

settings = get_settings()


@pytest_asyncio.fixture
async def ctx():
    """Fresh DB + in-memory Qdrant + fake embedder/LLM per test."""
    SPANS.clear()
    await init_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    store = VectorStore(AsyncQdrantClient(location=":memory:"), "chunks", FakeEmbedder.dim)
    await store.ensure_collection()
    app.state.embedder = FakeEmbedder()
    app.state.store = store
    app.state.storage = get_storage()
    app.state.llm = FakeLLM()
    app.state.reranker = FakeReranker()

    redis = FakeAsyncRedis(decode_responses=True)  # real Redis semantics, in memory, fresh per test
    app.state.redis = redis
    app.state.cache = AnswerCache(redis, settings.cache_ttl_s)
    app.state.quota = TokenQuota(redis, settings.daily_token_quota)
    app.state.limiter = RateLimiter(redis)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
