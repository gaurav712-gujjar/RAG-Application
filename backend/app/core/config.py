from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All config comes from environment variables (or a local .env for dev)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- app / auth ---
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:3000"

    # --- uploads ---
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 25

    # --- vector store ---
    qdrant_url: str = "http://localhost:6333"  # use ":memory:" for tests
    qdrant_api_key: str = ""  # required by Qdrant Cloud / secured clusters
    # v2 schema = named vectors (dense + bm25). Phase 1/2 data used "chunks": re-upload those docs.
    qdrant_collection: str = "chunks_hybrid"

    # --- embeddings ---
    gemini_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_provider: str = "local" 

    # --- chunking (chars ~= 4 per token, so 2000 chars ~= 500 tokens) ---
    chunk_size_chars: int = 2000
    chunk_overlap_chars: int = 200

    # --- retrieval ---
    retrieve_top_k: int = 8
    context_top_n: int = 3
    relevance_threshold: float = 0.35

    hybrid_enabled: bool = True  # dense + BM25 fused with RRF
    rerank_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3"  # multilingual cross-encoder
    # With the reranker on, the threshold applies to its 0-1 score; otherwise to dense cosine.
    rerank_threshold: float = 0.2
    # Latency knobs: the reranker reads every candidate in full, so fewer and shorter = faster (esp. on CPU)
    rerank_candidates: int = 6  # only the best N hybrid candidates go to the reranker
    rerank_max_length: int = 384  # tokens per (query, passage) pair; chunks beyond this are truncated
    # Without a reranker the dense cosine can be low for a query in another script/language (e.g.
    # romanised Hinglish vs an English chunk). A strong keyword match still counts as relevant.
    keyword_rescue_min_score: float = 1.0  # BM25 score needed; 0 disables the rescue
    keyword_rescue_top_n: int = 2  # only the best N keyword matches can be rescued
    rewrite_enabled: bool = True  # LLM rewrite of follow-up questions into standalone queries

    # --- cache / quota / rate limits (all need Redis; they fail open if Redis is down) ---
    cache_enabled: bool = True
    cache_ttl_s: int = 3600
    daily_token_quota: int = 50_000  # per user per UTC day; 0 = unlimited
    rate_limit_chat_per_min: int = 20
    rate_limit_upload_per_min: int = 10
    rate_limit_auth_per_min: int = 10  # per client IP (login/register)

    # --- LLM (Groq) ---
    groq_api_key: str = ""
    llm_model_fast: str = "llama-3.1-8b-instant"
    llm_model_strong: str = "llama-3.3-70b-versatile"
    max_output_tokens: int = 400
    temperature: float = 0.1
    history_turns: int = 2
    llm_max_retries: int = 3
    llm_timeout_s: float = 30.0
    # For reasoning models (openai/gpt-oss-20b): "low" = faster, fewer tokens. Empty = not sent.
    llm_reasoning_effort: str = ""

    # --- observability ---
    log_level: str = "INFO"
    log_format: str = "json"  # "json" (production) or "text" (readable in a terminal)
    metrics_enabled: bool = True
    metrics_token: str = ""  # if set, GET /metrics requires "Authorization: Bearer <token>"
    worker_metrics_port: int = 9100
    # Tracing is on when OTEL_EXPORTER_OTLP_ENDPOINT is set (works with Langfuse, Tempo, Jaeger...)
    otel_service_name: str = "docchat-api"
    trace_content: bool = False  # include question/answer text in spans (off: they are user data)

    # --- database ---
    # True: create tables on startup (dev/tests). False in production: run `alembic upgrade head`.
    db_auto_create: bool = True

    # --- ingestion ---
    # "inline": process inside the upload request (dev/tests, no Redis needed)
    # "celery": queue to a worker (production)
    ingest_mode: str = "inline"
    redis_url: str = "redis://localhost:6379/0"

    # --- file storage ("local" uses upload_dir, "s3" works with AWS S3 and MinIO) ---
    storage_backend: str = "local"
    s3_endpoint: str = ""  # e.g. http://minio:9000 ; empty = real AWS
    s3_bucket: str = "docchat"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    # --- OCR fallback for PDF pages with little/no text ---
    ocr_enabled: bool = True
    ocr_lang: str = "eng+hin"
    ocr_dpi: int = 300
    ocr_min_chars: int = 50  # pages with fewer extracted chars are OCR'd
    ocr_max_pages: int = 100  # cost/time guard per document


@lru_cache
def get_settings() -> Settings:
    return Settings()

