# DocChat — Chat with your documents (RAG)

Upload PDFs, Word files, text or images. Ask questions in English, Hindi or Hinglish. Get streamed answers with **citations** pointing at the exact passage, plus optional **live web search**.

Live stack runs on a **100% free, no-card** setup: Vercel + Render + Neon + Qdrant Cloud + Upstash + Groq + Gemini + Tavily.

---

## Table of contents
1. [What it does](#1-what-it-does)
2. [Tech stack](#2-tech-stack)
3. [How it was built](#3-how-it-was-built)
4. [Architecture](#4-architecture)
5. [How a document is processed](#5-how-a-document-is-processed)
6. [How a question is answered](#6-how-a-question-is-answered)
7. [Advantages](#7-advantages)
8. [Real-world use cases](#8-real-world-use-cases)
9. [Project structure (every file and folder)](#9-project-structure)
10. [Run locally](#10-run-locally)
11. [Deploy (free)](#11-deploy-free)
12. [Environment variables](#12-environment-variables)
13. [Testing and evaluation](#13-testing-and-evaluation)
14. [Security notes](#14-security-notes)
15. [Limitations and roadmap](#15-limitations-and-roadmap)

---

## 1. What it does

- **Accounts**: register/login (JWT). Every user only sees their own documents.
- **Upload** PDF, DOCX, TXT, MD (and scanned pages via OCR, English + Hindi).
- **Live pipeline view**: uploaded → parsing → splitting → embedding → indexing → done, with timings.
- **Chat with citations**: answers stream token by token; source cards open the exact passage.
- **Three modes**: `docs` (only your files), `docs_web` (files, then web if not found), `web` (web only).
- **Agent mode**: the model plans its own steps with tools (search documents, search web, calculator, list documents), up to 4 rounds, and shows each step.
- **Document filter**: ask against one or several chosen documents.
- **Hindi / Hinglish aware**: replies in the language you wrote in.
- **Suggested questions** generated per document.
- **Chat history** saved on the server (threads in sidebar).
- **Smalltalk shortcut**: "hi", "thanks" get an instant reply — no search, no LLM tokens.
- **Voice input** (browser Web Speech API).
- **Speed badge**: Time-To-First-Token, retrieval time, total time.
- **Limits and cache**: daily token quota, rate limits, answer cache (Redis).
- **Admin usage page** (`/admin`, protected by a token) and optional Sentry error tracking.

## 2. Tech stack

| Layer | Technology | Why |
|---|---|---|
| Frontend | Next.js 14, React, Tailwind CSS, react-markdown | Fast UI, SSR-ready, easy Vercel deploy |
| API | FastAPI (async), Pydantic | Async + SSE streaming, typed |
| Auth | JWT + bcrypt (10 rounds, off the event loop) | Simple, stateless |
| Database | PostgreSQL (Neon) / SQLite locally, SQLAlchemy async, Alembic | Users, documents, threads, usage |
| Vector DB | Qdrant (named vectors: dense + BM25 sparse) | Hybrid search in one store |
| Embeddings | Gemini `gemini-embedding-001` (768-d); FastEmbed / sentence-transformers as alternatives | Free hosted, strong multilingual |
| LLM | Groq `openai/gpt-oss-20b` | Very low latency, free tier |
| Web search | Tavily (primary), DuckDuckGo fallback | Reliable results, graceful fallback |
| Cache / limits | Redis (Upstash) | Quota, rate limit, answer cache; **fails open** |
| OCR | Tesseract (eng+hin) via pdf2image | Scanned PDFs and images |
| Background jobs | Inline by default, Celery optional | Free hosts have no worker |
| Observability | Prometheus metrics, Grafana, Sentry (optional) | Monitoring |
| CI/CD | GitHub Actions, Docker, Kubernetes manifests | Reproducible deploys |
| Hosting | Render (API), Vercel (UI) | Free tier |

## 3. How it was built

The project grew in phases:

1. **Core RAG** — upload, parse, chunk, embed, store in Qdrant, retrieve top chunks, stream a Groq answer with citations and a TTFT badge.
2. **Quality** — hybrid retrieval (dense + BM25) with RRF fusion, optional reranker, follow-up question rewriting, OCR for scans, golden-set evaluation.
3. **Production hardening** — per-user isolation on every query, Redis quota/rate limit/cache with circuit breaker, Alembic migrations, Prometheus/Grafana, Docker, Kubernetes, CI.
4. **Free deployment** — swapped local models for hosted Gemini embeddings (no RAM-hungry model on a 512 MB host), Neon, Qdrant Cloud, Upstash, Render + Vercel.
5. **Product polish** — AnythingLLM-style UI, server-side chat threads, web search modes, Markdown answers, passage viewer, voice input, suggested questions, smalltalk skip, admin page, Sentry.

Choices driven by constraints: free tier only → no card, small RAM, sleeping server, so heavy local models and paid GPUs were replaced by hosted APIs.

## 4. Architecture

```
Browser (Next.js on Vercel)
   │  HTTPS + SSE
   ▼
FastAPI (Render) ──► Neon Postgres   users, documents, threads, suggestions
   │            ├──► Qdrant Cloud    chunk vectors (dense + sparse)
   │            ├──► Upstash Redis   quota, rate limit, cache
   │            ├──► Gemini API      embeddings
   │            ├──► Groq API        answer generation
   │            └──► Tavily / DDG    web search
```

## 5. How a document is processed

```
Upload
  → validate (magic bytes, size)             parsers/files.py
  → store file (local disk or S3/MinIO)      core/storage.py
  → DB row, status "uploaded"
  → parsing     PDF/DOCX/TXT/MD text; OCR (Tesseract) for scanned pages
  → splitting   page-aware recursive splitter, 400 chars / 60 overlap
  → embedding   Gemini dense vectors (batches of 100, L2-normalised)
                + BM25 sparse terms
  → indexing    upsert to Qdrant with user_id, doc_id, page, text
  → done        timings saved; user's cached answers invalidated
                suggested questions generated (one LLM call)
```
Each stage is written to the DB with timings, so the UI shows a live progress bar. Failures mark the document `failed` with a reason.

## 6. How a question is answered

```
POST /chat  (SSE)
  1. smalltalk? ("hi", "thanks") → canned reply, stop
  2. rate limit → cache lookup → token quota check
  3. follow-up? → LLM rewrites into a standalone query
  4. embed query → dense search + BM25 search (user_id [+ doc_ids] filter)
  5. RRF fusion → keyword rescue → (optional rerank) → top passages
  6. no good passages and mode allows it → web search (Tavily/DDG)
  7. build compact prompt (language-aware, Markdown allowed) → Groq stream
  8. stream events: stage → sources → ttft → token… → done
  9. record tokens, cache the answer, save the thread
```
SSE events: `stage`, `sources`, `ttft`, `token`, `done` (with `ttft_ms`, `retrieval_ms`, `answer_source`, …), `error`.

## 7. Advantages

- **Grounded answers**: every claim comes with a source passage — easy to verify, fewer hallucinations.
- **Private**: strict per-user filtering at the vector-store level.
- **Fast**: Groq streaming gives sub-second first token.
- **Hybrid search**: semantic + keyword catches both meaning and exact terms (names, IDs, numbers).
- **Multilingual**: English, Hindi, Hinglish queries and OCR.
- **Free to run**: whole stack on free tiers.
- **Resilient**: Redis, web search and optional features fail soft; the core chat keeps working.
- **Observable and testable**: metrics, Sentry, 161 tests, golden-set evals.

## 8. Real-world use cases

- **Students**: ask questions across notes, textbooks and papers.
- **Support teams**: answer from policy, warranty and shipping documents.
- **HR / legal / finance**: find clauses and figures in long contracts and reports.
- **Researchers**: quickly query and cite papers.
- **Small business**: internal knowledge base without a custom ML team.
- **Hindi-speaking users**: query documents in Hinglish.

## 9. Project structure

### Root
| Path | Purpose |
|---|---|
| `README.md` | This file |
| `.env.example` | Template of all environment variables (no secrets) |
| `docker-compose.yml` | Full local stack: Postgres, Redis, Qdrant, MinIO, API, worker, migrate, frontend (+ observability profile) |
| `render.yaml` | Render deploy blueprint for the API |
| `pyproject.toml`, `uv.lock`, `.python-version` | Python project metadata and pinned deps (uv) |
| `.dockerignore`, `.gitignore` | Ignore rules |
| `.github/workflows/ci.yml` | Run tests on every push/PR |
| `.github/workflows/eval.yml` | Run retrieval/answer evaluation |
| `.github/workflows/release.yml` | Build and publish images |
| `src/rag_application/` | Package stub from the uv template |

### `backend/` — FastAPI service
| Path | Purpose |
|---|---|
| `Dockerfile` (`DockerfileOLD`) | Production image (old version kept for reference) |
| `.gcloudignore` | Files excluded for Cloud Run builds |
| `alembic.ini`, `alembic/` | DB migrations: `0001` initial schema, `0002` document stage, `0003` threads + suggestions |
| `requirements*.txt` | Runtime, dev, and eval dependencies |
| `pytest.ini` | Test config |

**`backend/app/`**
| Path | Purpose |
|---|---|
| `main.py` | App factory: routers, CORS, middleware, Sentry, LLM/embedder wiring |
| `api/documents.py` | Upload, list, delete, pipeline status |
| `api/chat.py` | SSE chat endpoint: modes, smalltalk, cache, quota, web fallback |
| `api/threads.py` | Server-side chat history |
| `api/suggestions.py` | Suggested questions per document |
| `api/usage.py` | Current user's token usage |
| `api/admin.py` | Admin usage stats (`X-Admin-Token`) |
| `auth/router.py`, `auth/deps.py` | Register/login/me; `get_current_user` dependency |
| `core/config.py` | Settings from environment |
| `core/db.py` | Async engine/session, auto-create |
| `core/security.py` | Password hashing, JWT |
| `core/storage.py` | File storage: local or S3/MinIO |
| `core/cache.py` | Redis answer cache with circuit breaker |
| `core/limits.py` | Token quota and rate limiting |
| `core/middleware.py`, `logging.py`, `metrics.py`, `tracing.py` | Request IDs, structured logs, Prometheus, tracing |
| `models/models.py` | SQLAlchemy tables: User, Document, ChatThread, DocSuggestion, … |
| `parsers/files.py` | Magic-byte validation and text extraction |
| `ocr/tesseract.py` | OCR for scanned pages (eng+hin) |
| `chunking/splitter.py` | Page-aware chunk splitter |
| `embeddings/factory.py` | Picks embedder via `EMBEDDING_PROVIDER` |
| `embeddings/gemini.py` | Hosted Gemini embedder (batching, retry, normalise) |
| `embeddings/fast.py`, `embedder.py`, `hashing.py` | FastEmbed, sentence-transformers, and test/hash embedders |
| `retrieval/store.py` | Qdrant client, upsert, filtered search |
| `retrieval/sparse.py` | BM25 sparse vectors |
| `retrieval/fusion.py` | Reciprocal Rank Fusion |
| `retrieval/rerank.py` | Optional cross-encoder reranker |
| `retrieval/search.py` | Full retrieval pipeline |
| `llm/groq_client.py` | Groq streaming/complete, retry, reasoning effort |
| `llm/prompts.py` | System prompts, language selection, message builders |
| `llm/rewrite.py` | Follow-up → standalone query |
| `llm/smalltalk.py` | Instant replies for greetings/thanks |
| `websearch/search.py` | Provider dispatcher |
| `websearch/tavily.py`, `ddg.py` | Tavily and DuckDuckGo backends |
| `workers/pipeline.py` | Ingestion stages and status updates |
| `workers/ingest.py` | Ingest entry point |
| `workers/dispatch.py` | Inline vs Celery dispatch |
| `workers/tasks.py`, `celery_app.py` | Optional Celery worker |

**`backend/evals/`** — quality measurement: `dataset.py`, `metrics.py`, `pipeline.py`, `ragas_judge.py`, `run_eval.py`, `hinglish_embed_check.py` (compare embedders on Hinglish), `golden/` (sample docs + `qa.jsonl`).

**`backend/tests/`** — 161 tests: ingestion, chunking, hybrid retrieval, isolation between users, cache/quota/limits, Redis breaker, TTFT, OCR, migrations, Tavily, Gemini, web modes, env-example check, and helpers (`conftest.py`, `fakes.py`, …).

### `frontend/` — Next.js app
| Path | Purpose |
|---|---|
| `app/page.tsx` | Main shell: sidebar + chat |
| `app/login/`, `app/register/` | Auth pages |
| `app/admin/` | Admin usage dashboard |
| `app/layout.tsx`, `globals.css` | Root layout, fonts, theme variables, Markdown styles |
| `components/Chat.tsx` | Streaming chat, composer, tools menu, sources, mic |
| `components/Sidebar.tsx` | Threads list and navigation |
| `components/DocsModal.tsx`, `UploadPanel.tsx`, `DocPipeline.tsx` | Document manager, upload, live pipeline |
| `components/Markdown.tsx` | Safe Markdown rendering |
| `components/UsageBadge.tsx`, `AuthForm.tsx`, `icons.tsx` | Small UI parts |
| `lib/api.ts` | Typed API client and SSE reader |
| `lib/threads.ts`, `lib/pipeline.ts` | Thread and pipeline helpers |
| `Dockerfile`, `package.json`, `tailwind.config.ts`, `next.config.mjs` | Build/config |

### `deploy/`
| Path | Purpose |
|---|---|
| `k8s/` | Kubernetes manifests (api, worker, frontend, ingress, migrate job, config, example secret) |
| `observability/` | Prometheus config, alert rules, Grafana dashboards |

## 10. Run locally

```bash
git clone https://github.com/gaurav712-gujjar/rag-application.git
cd rag-application
cp .env.example .env          # fill GROQ_API_KEY, GEMINI_API_KEY (and optional TAVILY_API_KEY)

# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend (new terminal)
cd frontend
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm install && npm run dev
```
Or the whole stack: `docker compose up --build`.

## 11. Deploy (free)

| Service | Role | Notes |
|---|---|---|
| Neon | Postgres | `DATABASE_URL=postgresql+asyncpg://…?ssl=require` |
| Qdrant Cloud | Vectors | `QDRANT_URL`, `QDRANT_API_KEY` |
| Upstash | Redis | `REDIS_URL=rediss://default:PASSWORD@host:6379` |
| Groq / Gemini / Tavily | LLM / embeddings / web | API keys |
| Render | API | Free web service; sleeps after 15 min idle — ping `/health` with UptimeRobot |
| Vercel | Frontend | Root directory `frontend`, `NEXT_PUBLIC_API_URL=<render url>` |

Set Render `CORS_ORIGINS` to the exact Vercel URL, no trailing slash. A sleeping Render service shows up as a CORS/"Failed to fetch" error.

## 12. Environment variables

See `.env.example` for the full list. Key ones:

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | SQLAlchemy URL (Postgres or SQLite) |
| `JWT_SECRET` | Long random string for tokens |
| `GROQ_API_KEY`, `LLM_REASONING_EFFORT` | LLM access; `low` for faster answers |
| `EMBEDDING_PROVIDER` | `gemini` \| `fastembed` \| `local` |
| `GEMINI_API_KEY` | Hosted embeddings |
| `QDRANT_URL`, `QDRANT_API_KEY` | Vector store |
| `REDIS_URL` | Quota/limits/cache (empty = disabled, fails open) |
| `TAVILY_API_KEY` | Web search (empty = DuckDuckGo) |
| `CORS_ORIGINS` | Allowed frontend origin |
| `ADMIN_TOKEN` | Enables `/admin/usage` |
| `SENTRY_DSN` | Error tracking |
| `INGEST_MODE` | `inline` or `celery` |

## 13. Testing and evaluation

```bash
cd backend && pip install -r requirements-dev.txt && pytest      # 161 tests
python -m evals.run_eval                                         # golden-set retrieval/answer eval
GEMINI_API_KEY=… python -m evals.hinglish_embed_check            # Hinglish embedding comparison
```
### Measure retrieval quality (hit-rate / MRR)

```bash
cd backend
# uses the same embedder as the app (EMBEDDING_PROVIDER in .env)
python -m evals.run_eval                 # hybrid (dense + BM25)
python -m evals.run_eval --dense-only    # dense only, to compare
```
Reports `hit_at_1`, `hit_at_3`, `mrr` and abstain rates on the 20-question golden set (English, Hindi, keyword, semantic).

CI runs tests on every push; `eval.yml` runs the evaluation.

## 14. Security notes

- **Never commit `.env` or keys.** Keep secrets in Render/Vercel environment settings.
- Rotate any secret that was ever shared in chat or logs.
- Every vector query is filtered by `user_id`; uploads are validated by magic bytes.
- Rate limits and daily token quotas protect free-tier API budgets.
- Admin endpoint is disabled unless `ADMIN_TOKEN` is set.

## 15. Limitations and roadmap

- Free Render sleeps when idle → first request after idle is slow.
- Reranker is off on the free tier (RAM).
- Ideas: table-aware parsing, per-document sharing, conversation memory summaries, more file types, usage analytics.

---

Built by [gaurav712-gujjar](https://github.com/gaurav712-gujjar).
