"""Per-scope document identity: (claim_id, filename), not filename alone.

Covers the SQLite store directly (coexistence, scoped overwrite, scoped
delete, legacy-DB migration) and the API surface (content/download resolving
the right scope, and the no-existence-oracle 403-vs-404 split for a claim a
caller cannot access).
"""
import sqlite3

import numpy as np
import pytest
from dataclasses import replace
from fastapi.testclient import TestClient

from backend.rag_engine import SQLiteVectorStore


class FakeEmbeddingEngine:
    """Deterministic 384-dim embeddings from a seeded hash of the text (no torch)."""

    def _embed(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        vec = rng.standard_normal(384).astype(np.float32)
        return vec / np.linalg.norm(vec)

    def embed_chunks(self, chunks):
        return [self._embed(c) for c in chunks]

    def embed_query(self, query):
        return self._embed(query)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return SQLiteVectorStore(
        db_path=str(tmp_path / "test.db"),
        storage_dir=str(tmp_path / "stored_documents"),
    )


@pytest.fixture
def embedder():
    return FakeEmbeddingEngine()


# ---------------------------------------------------------------------------
# Store-level: coexistence, scoped overwrite, scoped delete
# ---------------------------------------------------------------------------

def test_same_filename_coexists_in_claim_a_claim_b_and_global(store, embedder):
    store.add_document("report.pdf", "pdf", 7, "claim A content", embedder, claim_id="claim-a")
    store.add_document("report.pdf", "pdf", 7, "claim B content", embedder, claim_id="claim-b")
    store.add_document("report.pdf", "pdf", 7, "global content", embedder)

    assert store.get_document_content("report.pdf", "claim-a") == "claim A content"
    assert store.get_document_content("report.pdf", "claim-b") == "claim B content"
    assert store.get_document_content("report.pdf", None) == "global content"

    assert [d["filename"] for d in store.get_claim_documents("claim-a")] == ["report.pdf"]
    assert [d["filename"] for d in store.get_claim_documents("claim-b")] == ["report.pdf"]
    assert [d["filename"] for d in store.get_all_documents()] == ["report.pdf"]


def test_overwrite_within_scope_replaces_only_that_scope(store, embedder):
    store.add_document("report.pdf", "pdf", 7, "claim A v1", embedder, claim_id="claim-a")
    store.add_document("report.pdf", "pdf", 7, "claim B v1", embedder, claim_id="claim-b")

    store.add_document("report.pdf", "pdf", 7, "claim A v2", embedder, claim_id="claim-a")

    assert store.get_document_content("report.pdf", "claim-a") == "claim A v2"
    assert store.get_document_content("report.pdf", "claim-b") == "claim B v1"
    # Still exactly one row per scope -- the overwrite didn't duplicate.
    assert len(store.get_claim_documents("claim-a")) == 1


def test_delete_in_claim_a_leaves_b_and_global_intact(store, embedder):
    store.add_document("report.pdf", "pdf", 7, "claim A content", embedder, claim_id="claim-a")
    store.add_document("report.pdf", "pdf", 7, "claim B content", embedder, claim_id="claim-b")
    store.add_document("report.pdf", "pdf", 7, "global content", embedder)

    assert store.delete_document("report.pdf", "claim-a") is True

    assert store.get_claim_documents("claim-a") == []
    assert store.get_document_content("report.pdf", "claim-b") == "claim B content"
    assert store.get_document_content("report.pdf", None) == "global content"
    # Deleting the same scope again is a no-op, not a cross-scope deletion.
    assert store.delete_document("report.pdf", "claim-a") is False
    assert store.get_document_content("report.pdf", "claim-b") == "claim B content"


# ---------------------------------------------------------------------------
# Legacy-DB migration: `filename TEXT UNIQUE` rebuilt to the scope-keyed index
# ---------------------------------------------------------------------------

def _build_legacy_db(db_path):
    """Hand-builds a documents table using the pre-migration schema (filename
    globally UNIQUE), with one parent/child chunk so the migration's id
    preservation can be checked against the FK link."""
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
        conn.execute("""
            CREATE TABLE parent_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER,
                chunk_index INTEGER,
                content TEXT,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE child_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER,
                content TEXT,
                embedding BLOB,
                FOREIGN KEY (parent_id) REFERENCES parent_chunks (id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE VIRTUAL TABLE parent_chunks_fts USING fts5(content, tokenize='porter')")
        conn.execute(
            "INSERT INTO documents (id, filename, file_type, file_size, uploaded_at, claim_id) "
            "VALUES (5, 'legacy.txt', 'txt', 9, '2026-01-01 00:00:00', NULL)"
        )
        conn.execute(
            "INSERT INTO parent_chunks (id, document_id, chunk_index, content) VALUES (11, 5, 0, 'legacy content')"
        )
        conn.execute("INSERT INTO parent_chunks_fts (rowid, content) VALUES (11, 'legacy content')")
        conn.execute(
            "INSERT INTO child_chunks (id, parent_id, content, embedding) VALUES (21, 11, 'legacy content', ?)",
            (np.zeros(384, dtype=np.float32).tobytes(),),
        )
        conn.commit()
    finally:
        conn.close()


def test_legacy_db_migrates_and_keeps_data_and_chunk_links(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    _build_legacy_db(db_path)

    store = SQLiteVectorStore(db_path=db_path, storage_dir=str(tmp_path / "stored_documents"))

    # The legacy UNIQUE constraint is gone; the scope-keyed index is in place.
    conn = sqlite3.connect(db_path)
    try:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone()[0]
        assert "UNIQUE" not in table_sql
        index_names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        assert "ix_documents_scope" in index_names
    finally:
        conn.close()

    # Data and the parent_chunks.document_id FK link both survived (id=5 kept).
    assert store.get_document_content("legacy.txt", None) == "legacy content"
    row = sqlite3.connect(db_path).execute("SELECT id FROM documents WHERE filename = 'legacy.txt'").fetchone()
    assert row[0] == 5
    parent_row = sqlite3.connect(db_path).execute(
        "SELECT document_id FROM parent_chunks WHERE id = 11"
    ).fetchone()
    assert parent_row[0] == 5

    # A same-named document can now be added in a different (claim) scope.
    store.add_document("legacy.txt", "txt", 9, "claim content", FakeEmbeddingEngine(), claim_id="claim-a")
    assert store.get_document_content("legacy.txt", "claim-a") == "claim content"
    assert store.get_document_content("legacy.txt", None) == "legacy content"

    # Re-running _init_db (a second store open against the same file) is a no-op.
    store2 = SQLiteVectorStore(db_path=db_path, storage_dir=str(tmp_path / "stored_documents"))
    assert store2.get_document_content("legacy.txt", None) == "legacy content"
    assert store2.get_document_content("legacy.txt", "claim-a") == "claim content"


# ---------------------------------------------------------------------------
# API: content/download resolve the right scope; claim access is checked
# before the scoped lookup (no existence oracle).
# ---------------------------------------------------------------------------

def local_settings(tmp_path, **kw):
    from config import Settings
    return replace(Settings(), rag_db_path=tmp_path / 'rag.db',
                   stored_documents_dir=tmp_path / 'docs', jobs_db_path=tmp_path / 'jobs.db',
                   audit_log_path=tmp_path / 'audit.jsonl', llm_provider='none', **kw)


def _restricted_app(tmp_path):
    from app_factory import create_app
    from backend.authn import ServiceAccountAuthenticator
    from backend.rbac import ClaimAccessPolicy

    application = create_app(local_settings(tmp_path, tenant_id='tenant-a'))
    rt = application.state.runtime
    rt._authenticator = ServiceAccountAuthenticator({
        'alice': {'subject': 'alice', 'tenant_id': 'tenant-a', 'roles': ['adjuster']},
    })
    rt._claim_access_policy = ClaimAccessPolicy({'claim-a': ['alice'], 'claim-b': ['bob']})
    return application, rt


def test_content_and_download_resolve_correct_scope(tmp_path):
    application, rt = _restricted_app(tmp_path)
    rt.vector_store.add_document('report.pdf', 'pdf', 7, 'claim A content', FakeEmbeddingEngine(), claim_id='claim-a')
    rt.vector_store.add_document('report.pdf', 'pdf', 7, 'global content', FakeEmbeddingEngine())

    with TestClient(application) as client:
        headers = {'X-API-Key': 'alice'}
        content = client.get('/api/documents/content/report.pdf', params={'claim_id': 'claim-a'}, headers=headers)
        assert content.status_code == 200
        assert content.json()['content'] == 'claim A content'

        global_content = client.get('/api/documents/content/report.pdf', headers=headers)
        assert global_content.status_code == 200
        assert global_content.json()['content'] == 'global content'

        download = client.get('/api/documents/download/report.pdf', params={'claim_id': 'claim-a'}, headers=headers)
        assert download.status_code == 200
        assert download.content == b'claim A content'


def test_restricted_user_gets_403_not_404_for_other_claims_filename(tmp_path):
    application, rt = _restricted_app(tmp_path)
    rt.vector_store.add_document('report.pdf', 'pdf', 7, 'claim B content', FakeEmbeddingEngine(), claim_id='claim-b')

    with TestClient(application) as client:
        headers = {'X-API-Key': 'alice'}
        # Existing file in a claim alice can't access: 403, not 404.
        for path in ('/api/documents/content/report.pdf', '/api/documents/download/report.pdf'):
            response = client.get(path, params={'claim_id': 'claim-b'}, headers=headers)
            assert response.status_code == 403

        # Nonexistent file in that same inaccessible claim: still 403, not
        # 404 -- the lookup result must never be an existence oracle.
        for path in ('/api/documents/content/nope.pdf', '/api/documents/download/nope.pdf'):
            response = client.get(path, params={'claim_id': 'claim-b'}, headers=headers)
            assert response.status_code == 403
