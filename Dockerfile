# AutoClaimsRAG — single-image container for the FastAPI backend + static frontend.
#
# The app forces Hugging Face offline mode at import time (backend/app.py sets
# HF_HUB_OFFLINE=1), so the embedding + reranker models must already be in the
# HF cache before the server starts. This image pre-caches them at build time
# (scripts/precache_models.py, which needs network) so the resulting image
# never needs network access to boot -- matching the app's own "runs entirely
# locally" design constraint.
#
# LM Studio (the LLM) is a native desktop app, not something this image can
# contain -- point LLM_BASE_URL at the host (see docker-compose.yml) or leave
# LLM_PROVIDER unset/SIMULATION_MODE=true to run without a live model.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Layer-cache the (heavy: torch/transformers) dependency install separately
# from application code so code-only changes don't reinstall everything.
COPY requirements.txt .
RUN pip install --upgrade pip
# sentence-transformers pulls torch with no version/variant pin, so pip
# resolves the default CUDA build (~7GB of nvidia-cu13 wheels) even though
# this app is CPU-only end to end (embedding, reranking, generation all run
# via LM Studio, not in-process GPU inference). Installing the CPU-only wheel
# first satisfies that dependency before requirements.txt is processed.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install -r requirements.txt

# Pre-cache the embedding + cross-encoder reranker models (network required
# here only -- never again at runtime).
COPY scripts/precache_models.py scripts/precache_models.py
RUN python scripts/precache_models.py

COPY config.py app_factory.py ./
COPY backend/ backend/
COPY frontend/ frontend/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY generate_auto_pdfs.py create_sample_files.py ingest_all.py ./
COPY scripts/seed_demo.py scripts/collect_source_garbage.py scripts/

# Runtime data (rag_store.db, stored_documents/, jobs.db, audit.log.jsonl)
# defaults to relative paths under /app per .env.example; docker-compose.yml
# redirects them into /app/data and mounts a named volume there. That
# directory must already exist, owned by appuser, *before* the volume is
# mounted -- Docker seeds a fresh named volume from whatever is already at
# the mount point in the image, ownership included; an unmounted path there
# gets created by the daemon as root:root, which the non-root appuser below
# can't write to (verified empirically: sqlite3.OperationalError: unable to
# open database file without this).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)" || exit 1

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
