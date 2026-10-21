"""Tests for the DB-backed vector-cache generation (cross-process
invalidation), single-flight rebuilds, and parent-content dedupe in
SQLiteVectorStore (backend/rag_engine.py).

Deliberately torch-free: uses the same deterministic fake embedder pattern as
test_rag_engine.py, never loading the real ML stack.
"""
import sqlite3
import threading
import time

import numpy as np
import pytest

from backend.rag_engine import SQLiteVectorStore


class FakeEmbeddingEngine:
    """Deterministic 384-dim embeddings from a seeded hash of the text, so
    identical text always embeds identically and search is reproducible."""

    def _embed(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        vec = rng.standard_normal(384).astype(np.float32)
        return vec / np.linalg.norm(vec)

    def embed_chunks(self, chunks):
        return [self._embed(c) for c in chunks]

    def embed_query(self, query):
        return self._embed(query)


@pytest.fixture
def embedder():
    return FakeEmbeddingEngine()


LABOR_TEXT = (
    "Regional labor rate schedule 2026. The maximum allowed mechanical labor "
    "rate for Nevada is $110 per hour. Sheet metal repair is capped at $62 "
    "per hour and refinishing at $62 per hour statewide."
)
GLASS_TEXT = (
    "Zero-deductible glass endorsement. Windshield replacement with OEM spec "
    "glass waives the comprehensive deductible entirely for safety glass."
)


def _store_meta_generation(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT value FROM store_meta WHERE key = 'vector_generation'"
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cross-process invalidation (finding #3): two SQLiteVectorStore instances on
# the same db file stand in for two uvicorn workers.
# ---------------------------------------------------------------------------

def test_delete_through_one_instance_is_invisible_to_another(tmp_path, embedder):
    db_path = str(tmp_path / "shared.db")
    storage_dir = str(tmp_path / "stored_documents")
    store_a = SQLiteVectorStore(db_path=db_path, storage_dir=storage_dir)
    store_b = SQLiteVectorStore(db_path=db_path, storage_dir=storage_dir)

    store_a.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
    q = "labor rate"
    emb = embedder.embed_query(q)

    # Warm both instances' in-memory caches before the delete.
    assert store_a.search_similarity(emb, q, top_k=4)
    assert store_b.search_similarity(emb, q, top_k=4)

    store_a.delete_document("labor.txt")

    # store_b never received store_a's in-process _invalidate_vector_cache
    # call -- it must still detect the delete through store_meta.
    assert store_b.search_similarity(emb, q, top_k=4) == []


def test_add_through_one_instance_is_visible_to_another(tmp_path, embedder):
    db_path = str(tmp_path / "shared.db")
    storage_dir = str(tmp_path / "stored_documents")
    store_a = SQLiteVectorStore(db_path=db_path, storage_dir=storage_dir)
    store_b = SQLiteVectorStore(db_path=db_path, storage_dir=storage_dir)

    q = "glass deductible"
    emb = embedder.embed_query(q)
    # Warm store_b's cache while the store is still empty.
    assert store_b.search_similarity(emb, q, top_k=4) == []

    store_a.add_document("glass.txt", "txt", 100, GLASS_TEXT, embedder)

    results = store_b.search_similarity(emb, q, top_k=4)
    assert "glass.txt" in [r["filename"] for r in results]


def test_generation_increments_on_write_and_persists_across_reopen(tmp_path, embedder):
    db_path = str(tmp_path / "gen.db")
    storage_dir = str(tmp_path / "stored_documents")
    store = SQLiteVectorStore(db_path=db_path, storage_dir=storage_dir)

    assert _store_meta_generation(db_path) == 0
    store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
    after_add = _store_meta_generation(db_path)
    assert after_add > 0

    # Reopening the store (_init_db re-run) must not reset the counter.
    SQLiteVectorStore(db_path=db_path, storage_dir=storage_dir)
    assert _store_meta_generation(db_path) == after_add

    store.delete_document("labor.txt")
    assert _store_meta_generation(db_path) > after_add


# ---------------------------------------------------------------------------
# Single-flight rebuild (finding #11).
# ---------------------------------------------------------------------------

def test_single_flight_build_runs_once_under_concurrent_search(tmp_path, embedder):
    store = SQLiteVectorStore(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "stored_documents"))
    store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
    store.add_document("glass.txt", "txt", 100, GLASS_TEXT, embedder)

    build_calls = []
    calls_lock = threading.Lock()
    original_build = store._build_vector_cache

    def counted_build():
        with calls_lock:
            build_calls.append(1)
        # Widen the window so every thread below reaches the build-lock
        # acquire before the first one finishes and installs the cache.
        time.sleep(0.05)
        return original_build()

    store._build_vector_cache = counted_build

    q = "labor rate"
    emb = embedder.embed_query(q)
    thread_count = 8
    start_barrier = threading.Barrier(thread_count)

    def run():
        start_barrier.wait()
        store.search_similarity(emb, q, top_k=4)

    threads = [threading.Thread(target=run) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(build_calls) == 1


# ---------------------------------------------------------------------------
# Parent-content dedupe (finding #11).
# ---------------------------------------------------------------------------

def test_vector_cache_dedupes_parent_content(tmp_path, embedder):
    store = SQLiteVectorStore(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "stored_documents"))
    # Long enough that TextChunker splits it into several 250-char child
    # chunks under a single ~1200-char parent, so dedupe is actually
    # exercised (a single child per parent would pass trivially).
    text = " ".join(f"Clause {i} describes labor repair caps for region {i}." for i in range(60))
    store.add_document("doc.txt", "txt", len(text), text, embedder)

    q = "labor repair caps"
    assert store.search_similarity(embedder.embed_query(q), q, top_k=5)

    conn = sqlite3.connect(store.db_path)
    try:
        n_parents = conn.execute("SELECT COUNT(*) FROM parent_chunks").fetchone()[0]
        n_children = conn.execute("SELECT COUNT(*) FROM child_chunks").fetchone()[0]
    finally:
        conn.close()
    assert n_children > n_parents  # otherwise dedupe wouldn't be exercised

    cache = store._vector_cache
    assert "contents" not in cache  # old per-child-copy key is gone
    assert len(cache["parent_contents"]) == n_parents
    assert len(cache["parent_content_idx"]) == n_children
    assert all(isinstance(c, str) for c in cache["parent_contents"])


# ---------------------------------------------------------------------------
# Triggers must exist after the legacy-schema (`filename TEXT UNIQUE`)
# rebuild path in _init_db, since dropping a table drops its triggers.
# ---------------------------------------------------------------------------

def test_triggers_and_store_meta_survive_legacy_schema_migration(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE,
                file_type TEXT,
                file_size INTEGER,
                uploaded_at TEXT,
                claim_id TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()

    SQLiteVectorStore(db_path=db_path, storage_dir=str(tmp_path / "stored_documents"))

    conn = sqlite3.connect(db_path)
    try:
        trigger_names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
        table_names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()

    assert "store_meta" in table_names
    assert {
        "documents_bump_vector_generation_insert",
        "documents_bump_vector_generation_update",
        "documents_bump_vector_generation_delete",
    } <= trigger_names
    assert _store_meta_generation(db_path) == 0
