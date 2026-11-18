# DocChat: RAG over your documents (Phases 1-4)

Upload PDF, DOCX, TXT or MD files, then chat with them. Answers are streamed from Groq and
show a latency badge: `TTFT 0.35s · Retrieval 0.12s · Total 2.1s` (plus `cached` when served from cache).

## Architecture

```
Upload -> validate (magic bytes, size) -> store file (MinIO/S3) -> DB row "uploaded" -> Celery queue
Worker -> "processing" -> parse (+OCR for scanned pages) -> chunk -> embed (bge-m3) + BM25 terms -> Qdrant
       -> "ready" | "failed"  (and the user's cached answers are invalidated)

Chat   -> rate limit -> cache lookup (first questions only) -> token quota check
       -> [follow-up? LLM rewrites it into a standalone query]
       -> embed query -> dense search + BM25 search (both user_id-filtered) -> RRF fusion -> top-8
       -> bge-reranker -> drop below threshold -> top-3 -> compact prompt -> Groq (stream) -> SSE -> UI
       -> record tokens used, store answer in cache
```

SSE events from `POST /chat`: `sources` -> `ttft` -> `token`* -> `done` (or `error`).
`ttft` is its own event, sent the moment the first non-empty token arrives.

```
backend/app/
  api/         documents.py (upload/list/delete), chat.py (SSE + TTFT), usage.py
  auth/        JWT register/login/me, get_current_user
  parsers/     magic-byte validation, PDF/DOCX/TXT/MD extraction
  chunking/    page-aware RecursiveCharacterTextSplitter
  embeddings/  local bge-m3 (sentence-transformers)
  retrieval/   store.py (Qdrant, user_filter on every query), sparse.py (BM25 vectors),
               fusion.py (RRF), rerank.py (bge-reranker), search.py (retrieve pipeline)
  llm/         Groq client (stream + complete, retry/backoff), prompts, model routing, follow-up rewrite
  ocr/         Tesseract OCR (pdf2image + pytesseract, 300 DPI, grayscale/contrast/denoise)
  workers/     pipeline.py (status lifecycle), ingest.py, tasks.py + celery_app.py, dispatch.py
  core/        settings, db, security, storage (local + S3/MinIO), cache.py, limits.py (quota + rate limits)
  models/      SQLAlchemy models
frontend/      Next.js + Tailwind: login, upload panel, streaming chat, citations, latency badge, token usage
```

## Phase 3 features

| Feature | How it works |
|---|---|
| **Hybrid search** | Dense (bge-m3) + BM25 keyword search, fused with Reciprocal Rank Fusion. BM25 uses sparse vectors with Qdrant's IDF modifier, so there is no extra model; tokenization handles Hindi and other Indic scripts. Exact terms (invoice numbers, names, codes) that embeddings miss are now found. |
| **Reranker** | `BAAI/bge-reranker-v2-m3` (multilingual cross-encoder) re-scores the top-8 candidates; the relevance threshold then applies to its 0-1 score. If it errors, search falls back to dense scores. |
| **Answer cache** | Redis. Key = user + that user's document version + normalised question. Only first questions are cached (follow-ups depend on the conversation). Uploading or deleting a document bumps the version, so stale answers are never served. Cache hits use no LLM and no tokens. |
| **Token quota** | Per user per UTC day (`DAILY_TOKEN_QUOTA`, 0 = unlimited). Counts real usage from Groq (estimated if missing, and also when the client disconnects mid-answer). Over the limit: HTTP 429 with `Retry-After`; cached answers are still served. Shown in the UI header and at `GET /usage`. |
| **Rate limits** | Sliding window in Redis: chat and uploads per user, login/register per client IP. 429 with `Retry-After`. |
| **Follow-up rewrite** | A short question that refers back ("and how long?") is rewritten by the 8B model into a standalone search query. Standalone questions and first questions cost nothing extra. |

Cache, quota and rate limits **fail open**: if Redis is down they are skipped (with a warning log) and chat keeps working.

## Run with Docker

```bash
cp .env.example .env        # set GROQ_API_KEY and JWT_SECRET
docker compose up --build
```

- Services: api, worker (Celery), frontend, postgres, redis, qdrant, minio
- App: http://localhost:3000, API docs: http://localhost:8000/docs, MinIO console: http://localhost:9001 (minioadmin / minioadmin, change for real deployments)
- Uploads return `202`; the UI shows `uploaded -> processing -> ready/failed` while the worker runs.
- The image installs `tesseract-ocr` with English + Hindi packs. For more languages add `tesseract-ocr-<lang>` in `backend/Dockerfile` and extend `OCR_LANG`.
- First start downloads bge-m3 (~2 GB) and bge-reranker-v2-m3 (~2 GB), cached in the `hf_cache` volume, so the API takes a few minutes to become ready.
- **Upgrading from Phase 1/2:** the Qdrant collection schema changed (named dense + bm25 vectors) and now defaults to `chunks_hybrid`. Existing documents must be re-uploaded (or run `docker compose down -v` to start clean).
- Behind a reverse proxy, run uvicorn with `--proxy-headers` so the login rate limit sees real client IPs.

## Run locally (no Docker)

```bash
# Qdrant (+ Redis if you want cache/quota/rate limits)
docker run -p 6333:6333 qdrant/qdrant
docker run -p 6379:6379 redis:7-alpine

# Backend (uses SQLite, local file storage and INGEST_MODE=inline by default: no MinIO/worker needed;
# OCR needs the tesseract + poppler system packages, otherwise scanned PDFs fail with a clear message)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example .env     # set GROQ_API_KEY and JWT_SECRET
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

On a small machine set `RERANK_ENABLED=false` to skip the reranker (saves ~2 GB and some latency).

## Phase 4: observability, migrations, eval, CI/CD, deploy

**Logs.** JSON by default (`LOG_FORMAT=text` for dev). Every request gets an `X-Request-ID` (accepted from the caller if valid, else generated), included in each log line.

**Metrics.** `GET /metrics` (Prometheus). Set `METRICS_TOKEN` to require `Authorization: Bearer <token>`. Worker metrics on port `WORKER_METRICS_PORT` (9100). Key series: `chat_ttft_seconds{source}`, `chat_retrieval_seconds`, `chat_rerank_seconds`, `chat_requests_total{outcome}`, `llm_tokens_total{model,kind}`, `answer_cache_lookups_total`, `reranker_fallbacks_total`, `redis_unavailable_total`, `documents_ingested_total`, `http_*`. Do not expose `/metrics` publicly (the k8s ingress doesn't).

**Dashboards and alerts.** `docker compose --profile observability up` starts Prometheus (:9090) and Grafana (:3001, dashboard "DocChat overview" is provisioned). Alert rules: `deploy/observability/alerts.yml`.

**Tracing.** Set `OTEL_EXPORTER_OTLP_ENDPOINT` (+ `OTEL_EXPORTER_OTLP_HEADERS`) to export to Langfuse, Tempo, Jaeger, etc. Spans: `chat > rewrite_query, retrieval(embed_query, vector_search, rerank), llm` and `ingest_document > parse, embed, upsert`. Question/answer text is NOT put in spans unless `TRACE_CONTENT=true`.

**Migrations (Alembic).**

```bash
cd backend
alembic upgrade head                 # new database
alembic stamp head                   # database created earlier by Phase 1-3 create_all: adopt it, don't recreate
alembic revision --autogenerate -m "msg"
```

Production/compose/k8s run with `DB_AUTO_CREATE=false`; a one-shot `migrate` service/Job runs `alembic upgrade head` before api/worker start.

**Eval.**

```bash
cd backend
python -m evals.run_eval --fast                       # no model download, hashing embedder: smoke test of the harness only
python -m evals.run_eval --min hit_at_k=0.8 --min mrr=0.6   # real bge-m3 + reranker (downloads models)
python -m evals.run_eval --generate                   # also calls Groq to check answers
pip install -r requirements-eval.txt && python -m evals.run_eval --ragas   # LLM-judge metrics: costs Groq tokens
```

Golden set: `backend/evals/golden/` (16 answerable incl. 3 Hindi, 4 unanswerable). Results go to `evals/results/` (gitignored). Exit code 1 if a `--min` gate fails or can't be measured. `--fast` abstain metrics are not meaningful.

**CI/CD (`.github/workflows`).**
- `ci.yml`: backend tests, frontend typecheck + build, docker builds on push/PR.
- `release.yml`: on tag, builds and pushes images to GHCR. Optional deploy job runs only if repo variable `DEPLOY_ENABLED=true` and secret `KUBE_CONFIG` are set.
- `eval.yml`: manual/scheduled real-model eval (needs `GROQ_API_KEY` secret for `--generate`).

**Kubernetes (`deploy/k8s`, kustomize).**

```bash
cd deploy/k8s
# 1. set real image names
kustomize edit set image ghcr.io/OWNER/REPO/backend=ghcr.io/<you>/<repo>/backend:<tag> ghcr.io/OWNER/REPO/frontend=ghcr.io/<you>/<repo>/frontend:<tag>
# 2. create secrets (see secret.example.yaml; it is not part of the kustomization)
kubectl apply -f namespace.yaml && kubectl -n docchat create secret generic docchat-secrets --from-literal=JWT_SECRET=... --from-literal=GROQ_API_KEY=... # + DATABASE_URL, S3_*, QDRANT_API_KEY as in secret.example.yaml
# 3. edit configmap.yaml (QDRANT_URL, REDIS_URL, S3_ENDPOINT, CORS_ORIGINS) and ingress.yaml (host)
kubectl apply -k .
```

Postgres, Redis, Qdrant and S3 are expected as managed/external services. The migrate Job runs first; set `PRELOAD_MODELS=1` build arg to bake models into the backend image (faster cold start, ~4 GB larger).

### Not verified yet (no Docker daemon / GPU / API keys in the build sandbox)
`docker compose up` and image builds, real bge-m3/reranker/Groq/Redis/MinIO/Celery runs, Hindi OCR, real-model eval numbers (`RELEVANCE_THRESHOLD=0.35` and `RERANK_THRESHOLD=0.2` are starting guesses: calibrate with `evals.run_eval`), Python 3.11 (tested on 3.13), GitHub Actions runs, Grafana rendering, release deploy job, k8s resource sizes, Langfuse ingestion, RAGAS with a real judge.

## Tests

```bash
cd backend && pytest
```

Uses fake embedder/LLM/reranker, in-memory Qdrant and fakeredis, so no model download, API key or
Redis is needed. Covers chunking, per-user isolation (dense, hybrid, cache, quota, rate limits), TTFT,
upload validation, delete cascade, auth, OCR fallback, the queued ingestion flow, BM25/RRF/hybrid
search (incl. Hindi and IDF), reranking and its fallback, follow-up rewrite, cache invalidation,
token accounting (incl. client disconnect), rate limiting and Redis-outage behaviour.

## Token saving

top-8 retrieved, only top-3 above the relevance threshold sent; no relevant chunk means no LLM call;
short system prompt; compact `[n] file pPage: text` context; `max_tokens=400`; last 2 chat turns only;
8B model by default, 70B only for complex questions; repeated questions served from cache;
per-user daily quota; token usage logged per request.

## Known limits

- OCR covers PDFs only (not standalone images), at most `OCR_MAX_PAGES` pages per file, and is slow on big scans
- No UI "retry" button for failed documents (re-uploading the same file retries it)
- The reranker adds latency to TTFT (it runs before the first token); it shows up inside "Retrieval" in the badge
- The token quota is soft: a request that starts under the limit may finish and push usage slightly over it
- Single uvicorn process per container (Prometheus metrics are per-process); scale with replicas, not `--workers`
- Celery worker: keep `--concurrency=1` (metrics port 9100 is per process)
- Chunk size is measured in characters (2000 chars is roughly 500 tokens)
