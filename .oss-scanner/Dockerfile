# OSS Scanner build for ClaimsRAG (https://github.com/anthropics/oss-scanner).
# Built with the repository as the build context. Network is available only during this build, so the
# dependencies and the two local Hugging Face models are fetched here; the test suite itself needs neither
# network nor a model cache (tests/conftest.py replaces the ML boundary).
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf-cache

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, for layer caching. CPU-only torch keeps sentence-transformers from pulling CUDA wheels.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r /tmp/requirements.txt

# scanner contract: the checkout lives inside the image, at /src
COPY . /src
WORKDIR /src

# Pre-cache the embedding and reranker models so the app can start offline (it forces HF_HUB_OFFLINE=1).
RUN python scripts/precache_models.py

# Offline from here on, as in CI.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Run the suite so the image is known to work; a failing test does not abort the build.
# Postgres-gated tests skip without POSTGRES_DSN, as they do in the SQLite CI job.
RUN python -m pytest tests/ -q -p no:cacheprovider || echo "WARNING: pytest reported failures"
