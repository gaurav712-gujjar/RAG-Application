FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models

# OCR: tesseract engine + English/Hindi language packs, poppler for PDF -> image
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user. /models holds the downloaded embedding + reranker weights.
RUN useradd --create-home --uid 10001 app && mkdir /models && chown app:app /models

WORKDIR /srv

COPY requirements.txt .
# CPU-only torch first (the default wheel pulls ~2 GB of CUDA libs we don't need)
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

# Optional: bake the model weights into the image (~4 GB larger) so pods start fast and never
# depend on Hugging Face being reachable:  docker build --build-arg PRELOAD_MODELS=true ...
ARG PRELOAD_MODELS=false
ARG EMBEDDING_MODEL=BAAI/bge-m3
ARG RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RUN if [ "$PRELOAD_MODELS" = "true" ]; then \
      python -c "from huggingface_hub import snapshot_download as d; d('$EMBEDDING_MODEL'); d('$RERANKER_MODEL')" \
      && chown -R app:app /models; \
    fi

COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app

USER app
EXPOSE 8000

# One process per container (metrics are per-process; scale with replicas, not --workers).
# Access logs come from our own JSON middleware, so uvicorn's are off.
# The worker service overrides this with: celery -A app.workers.celery_app worker ...
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
