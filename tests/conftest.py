"""Explicit isolation fixtures for API contract tests, without model downloads."""

import hashlib

import pytest


@pytest.fixture
def isolated_api_runtime(tmp_path, monkeypatch):
    """Keep real routing/auth/storage, replacing only the ML boundary and paths.

    Opt-in: embedding-engine and factory integration tests retain their own
    dependencies. Fail immediately if these API tests attempt model loading.
    """
    from backend import api
    from backend.app import runtime
    from backend.audit import JsonlAuditSink
    from backend.rag_engine import SQLiteVectorStore

    class Embedder:
        def embed_query(self, text):
            return [value / 255.0 for value in hashlib.sha256(text.encode()).digest()]

        def embed_chunks(self, chunks):
            return [self.embed_query(text) for text in chunks]

    def unexpected_model_load(*args, **kwargs):
        raise AssertionError("API contract tests must not load an embedding model")

    monkeypatch.setattr(api, "EmbeddingEngine", unexpected_model_load)
    monkeypatch.setattr(runtime, "_get_embedding_engine", lambda: Embedder())
    monkeypatch.setattr(runtime, "_reranker", None)
    monkeypatch.setattr(runtime, "vector_store", SQLiteVectorStore(
        db_path=str(tmp_path / "rag.db"), storage_dir=str(tmp_path / "documents"),
    ))
    monkeypatch.setattr(runtime, "_audit_sink", JsonlAuditSink(tmp_path / "audit.jsonl"))
