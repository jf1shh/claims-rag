"""Offline load-harness smoke entry point; never a production app target.

Keep the real configured factory, auth, HTTP routes, and Postgres retrieval.
Use the seed corpus's deterministic vectors and omit model reranking so CI
can exercise orchestration with no cached/downloaded model weights.
"""

from app_factory import create_app
from scripts.seed_retrieval_load_corpus import _FakeEmbedder

app = create_app()
app.state.runtime.embedding_engine = _FakeEmbedder()
app.state.runtime._reranker = None
