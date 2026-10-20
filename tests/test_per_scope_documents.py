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


def test_search_similarity_results_carry_claim_id(store, embedder):
    store.add_document("report.pdf", "pdf", 7, "claim A content about repairs", embedder, claim_id="claim-a")
    store.add_document("policy.pdf", "pdf", 7, "global policy content about repairs", embedder)

    claim_results = store.search_similarity(
        embedder.embed_query("repairs"), "repairs", claim_id="claim-a", use_fts=True
    )
    by_filename = {r["filename"]: r for r in claim_results}
    assert by_filename["report.pdf"]["claim_id"] == "claim-a"
    assert by_filename["policy.pdf"]["claim_id"] is None


def test_get_claim_chunks_results_carry_claim_id(store, embedder):
    store.add_document("report.pdf", "pdf", 7, "claim A content", embedder, claim_id="claim-a")

    chunks = store.get_claim_chunks("claim-a")
    assert chunks
    assert all(c["claim_id"] == "claim-a" for c in chunks)


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


def test_delete_after_migration_still_removes_chunks(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    _build_legacy_db(db_path)

    store = SQLiteVectorStore(db_path=db_path, storage_dir=str(tmp_path / "stored_documents"))
    assert store.delete_document("legacy.txt", None) is True

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE filename = 'legacy.txt'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM parent_chunks WHERE id = 11").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM child_chunks WHERE id = 21").fetchone()[0] == 0
    finally:
        conn.close()


def test_stale_documents_new_from_a_failed_migration_does_not_brick_startup(tmp_path):
    """A migration that failed after CREATE TABLE documents_new autocommitted
    (pre-fix behavior) leaves a leftover documents_new next to the still-legacy
    documents table. The next startup must clean it up and complete the
    migration rather than failing with "table documents_new already exists"."""
    db_path = str(tmp_path / "legacy.db")
    _build_legacy_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # Simulate the partial-failure leftover: an empty documents_new table
        # sitting next to the still-UNIQUE legacy documents table.
        conn.execute("""
            CREATE TABLE documents_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                file_type TEXT,
                file_size INTEGER,
                uploaded_at TEXT,
                claim_id TEXT,
                storage_key TEXT,
                document_version TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()

    store = SQLiteVectorStore(db_path=db_path, storage_dir=str(tmp_path / "stored_documents"))
    assert store.get_document_content("legacy.txt", None) == "legacy content"

    conn = sqlite3.connect(db_path)
    try:
        table_names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "documents_new" not in table_names
    finally:
        conn.close()


def test_migration_preserves_autoincrement_high_water_mark_and_drops_orphans(tmp_path):
    """A legacy DB where the highest-ever document id (3) was deleted (so
    sqlite_sequence.seq=3 but max(id)=2) left orphan chunks for id 3. After
    migration, a new upload must get id 4, not reuse 3 and inherit the orphans."""
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
            "VALUES (1, 'doc1.txt', 'txt', 9, '2026-01-01 00:00:00', NULL)"
        )
        conn.execute(
            "INSERT INTO documents (id, filename, file_type, file_size, uploaded_at, claim_id) "
            "VALUES (2, 'doc2.txt', 'txt', 9, '2026-01-01 00:00:00', NULL)"
        )
        # id=3 was uploaded then deleted -- the row is gone, but sqlite_sequence
        # still remembers it as the high-water mark (the explicit-id inserts
        # above already created the row with seq=2; bump it to 3), and its
        # chunks were never cleaned up (the orphan this migration must drop).
        conn.execute("UPDATE sqlite_sequence SET seq = 3 WHERE name = 'documents'")
        conn.execute(
            "INSERT INTO parent_chunks (id, document_id, chunk_index, content) VALUES (31, 3, 0, 'orphan content')"
        )
        conn.execute("INSERT INTO parent_chunks_fts (rowid, content) VALUES (31, 'orphan content')")
        conn.execute(
            "INSERT INTO child_chunks (id, parent_id, content, embedding) VALUES (41, 31, 'orphan content', ?)",
            (np.zeros(384, dtype=np.float32).tobytes(),),
        )
        conn.commit()
    finally:
        conn.close()

    store = SQLiteVectorStore(db_path=db_path, storage_dir=str(tmp_path / "stored_documents"))

    # Orphan rows for the deleted id=3 are gone.
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM parent_chunks WHERE document_id = 3").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM child_chunks WHERE parent_id = 31").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM parent_chunks_fts WHERE rowid = 31"
        ).fetchone()[0] == 0
    finally:
        conn.close()

    # A new upload gets id 4 (the old seq=3 high-water mark preserved, not
    # reset to max(id)=2), and has none of the orphaned content.
    store.add_document("doc3.txt", "txt", 9, "fresh content", FakeEmbeddingEngine())
    row = sqlite3.connect(db_path).execute(
        "SELECT id FROM documents WHERE filename = 'doc3.txt'"
    ).fetchone()
    assert row[0] == 4
    parent_rows = sqlite3.connect(db_path).execute(
        "SELECT content FROM parent_chunks WHERE document_id = 4"
    ).fetchall()
    assert [r[0] for r in parent_rows] == ["fresh content"]


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


def test_delete_gets_403_not_404_for_claim_caller_cannot_access(tmp_path):
    application, rt = _restricted_app(tmp_path)
    rt.vector_store.add_document('report.pdf', 'pdf', 7, 'claim B content', FakeEmbeddingEngine(), claim_id='claim-b')

    with TestClient(application) as client:
        headers = {'X-API-Key': 'alice'}
        # Existing file in a claim alice can't access: 403, not 404.
        response = client.post('/api/delete', json={'filename': 'report.pdf', 'claim_id': 'claim-b'}, headers=headers)
        assert response.status_code == 403

        # Nonexistent file in that same inaccessible claim: still 403, not
        # 404 -- the delete endpoint must not be an existence oracle either.
        response = client.post('/api/delete', json={'filename': 'nope.pdf', 'claim_id': 'claim-b'}, headers=headers)
        assert response.status_code == 403


def test_legacy_db_without_chunk_tables_migrates(tmp_path):
    """A DB from before parent/child chunking (2026-07-16) has the UNIQUE
    documents table but no chunk or FTS tables. The orphan cleanup must skip
    them; otherwise the migration rolls back on "no such table" every start."""
    db_path = str(tmp_path / "pre_chunks.db")
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
        conn.execute(
            "INSERT INTO documents (id, filename, file_type, file_size, uploaded_at, claim_id) "
            "VALUES (7, 'old.txt', 'text', 3, '2026-07-01', 'CLM-1')"
        )
        conn.commit()
    finally:
        conn.close()

    store = SQLiteVectorStore(db_path=db_path, storage_dir=str(tmp_path / "stored_documents"))

    assert store.get_document_metadata("old.txt", "CLM-1")["document_id"] == "7"
    conn = sqlite3.connect(db_path)
    try:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone()[0]
        assert "UNIQUE" not in table_sql
    finally:
        conn.close()
