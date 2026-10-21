"""Phase 1 Postgres + pgvector backend tests.

Gated on a real database: they skip unless POSTGRES_DSN points at a reachable
Postgres with the `vector` extension. CI runs them against a `pgvector`
service container (wiring in .github/workflows/tests.yml). They self-provision
the schema via `alembic upgrade head`, so the migration itself is exercised as
part of the suite.

Tenants are randomized per test for isolation; each test cleans up its own rows.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

POSTGRES_DSN = os.environ.get("POSTGRES_DSN")
needs_postgres = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="POSTGRES_DSN not set; set it to a reachable Postgres + pgvector to run the PG backend tests",
)


def _hash_embed(text: str):
    """Deterministic 384-dim unit vector from an md5 of the text (torch-free),
    so identical passages embed identically and ranking is reproducible."""
    digest = hashlib.md5(text.encode("utf-8")).digest()
    vec = np.frombuffer(digest * 48, dtype=np.uint8)[:384].astype(np.float32) / 255.0
    vec += np.arange(384, dtype=np.float32) / 10000.0  # de-correlate identical bytes
    return vec / np.linalg.norm(vec)


class _FakeEmbedder:
    def embed_chunks(self, chunks):
        return [_hash_embed(c) for c in chunks]

    def embed_query(self, query):
        return _hash_embed(query)


def _connect(dsn):
    import psycopg

    return psycopg.connect(dsn, autocommit=False)


def _schema_ready(dsn) -> bool:
    try:
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as c:
            cur = c.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            return cur.fetchone() is not None
    except Exception:
        return False


def _provision_schema(dsn: str) -> None:
    """Runs `alembic upgrade head` against the DSN so the migration is itself
    exercised every time this suite runs. The migration creates the `vector`
    extension (if absent) plus all tables/indexes, so this both provisions the
    schema and installs the extension a fresh DB needs."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    old = os.environ.get("POSTGRES_DSN")
    os.environ["POSTGRES_DSN"] = dsn
    try:
        command.upgrade(cfg, "head")
    finally:
        if old is None:
            os.environ.pop("POSTGRES_DSN", None)
        else:
            os.environ["POSTGRES_DSN"] = old


if POSTGRES_DSN:
    # A reachable DSN with no `vector` extension means a brand-new database that
    # has not been migrated yet (e.g. a fresh CI service container). Instead of
    # skipping, prove the extension is genuinely unavailable by running the
    # migration once -- it creates the extension + schema. Tests then skip only
    # for a truly unreachable/misconfigured DSN.
    if not _schema_ready(POSTGRES_DSN):
        try:
            _provision_schema(POSTGRES_DSN)
        except Exception:
            needs_postgres = pytest.mark.skipif(
                True, reason=f"could not provision schema/vector extension at {POSTGRES_DSN}"
            )
    if not _schema_ready(POSTGRES_DSN):
        needs_postgres = pytest.mark.skipif(
            True, reason=f"vector extension still missing after provisioning at {POSTGRES_DSN}"
        )


@pytest.fixture(scope="module")
def provisioned_db():
    _provision_schema(POSTGRES_DSN)
    return POSTGRES_DSN


@pytest.fixture()
def store(provisioned_db, tmp_path):
    """A PostgresVectorStore on a fresh per-test tenant."""
    import uuid

    from backend.rag_engine import SQLiteVectorStore  # noqa: F401  (ensures package imports)
    from backend.postgres_store import PostgresVectorStore

    tenant = f"test-{uuid.uuid4().hex[:8]}"
    s = PostgresVectorStore(
        dsn=provisioned_db,
        tenant_id=tenant,
        storage_dir=str(tmp_path / "docs"),
        embedding_dimensions=384,
    )
    yield s
    # cleanup this tenant's rows (cascade removes chunks)
    conn = _connect(provisioned_db)
    try:
        conn.execute("DELETE FROM documents WHERE tenant_id = %s", (tenant,))
        conn.commit()
    finally:
        conn.close()


def _add_global(store, filename, text, claim_id=None):
    """Indexes a synthetic document and returns (doc_id, parent_count)."""
    return store.add_document(
        filename,
        "txt",
        len(text),
        text,
        _FakeEmbedder(),
        claim_id=claim_id,
    )


# --------------------------------------------------------------------------- #
# Blob-store wiring (Phase 2) -- source bytes route to the S3 adapter
# --------------------------------------------------------------------------- #

@needs_postgres
def test_blob_store_wiring_routes_bytes_to_object_store(provisioned_db, tmp_path):
    """With a blob_store attached, add/delete must put/delete the source object
    instead of writing to storage_dir (parity with SQLiteVectorStore)."""
    import uuid

    import boto3
    from moto import mock_aws

    from backend.blob_store import S3DocumentBlobStore
    from backend.postgres_store import PostgresVectorStore

    tenant = f"test-{uuid.uuid4().hex[:8]}"
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bucket")
        blob = S3DocumentBlobStore(bucket="test-bucket", tenant_id=tenant)
        s = PostgresVectorStore(
            dsn=provisioned_db,
            tenant_id=tenant,
            storage_dir=str(tmp_path / "docs"),
            embedding_dimensions=384,
            blob_store=blob,
        )
        try:
            text = "Nevada mechanical labor cap is 110 dollars per hour."
            s.add_document("labor.txt", "txt", len(text), text, _FakeEmbedder(), claim_id="claim-1")
            body = client.get_object(Bucket="test-bucket", Key=f"{tenant}/" + s.get_blob_key("labor.txt", "claim-1"))["Body"].read()
            assert body.decode() == text
            assert not (tmp_path / "docs" / "labor.txt").exists()
            assert s.get_blob_key("labor.txt", "claim-1").startswith("versions/")
            s.delete_document("labor.txt", "claim-1")
            keys = [o["Key"] for o in client.list_objects_v2(Bucket="test-bucket").get("Contents", [])]
            assert f"{tenant}/claim-1/labor.txt" not in keys
        finally:
            conn = _connect(provisioned_db)
            try:
                conn.execute("DELETE FROM documents WHERE tenant_id = %s", (tenant,))
                conn.commit()
            finally:
                conn.close()


# --------------------------------------------------------------------------- #
# Basic add / search / chunk / delete
# --------------------------------------------------------------------------- #

@needs_postgres
def test_add_search_and_delete_roundtrip(store):
    text = "Nevada mechanical labor cap is 110 dollars per hour. Sheet metal repair capped at 62 dollars per hour."
    doc_id, parents = _add_global(store, "regional_labor_rates.txt", text)
    assert isinstance(doc_id, int)
    assert parents >= 1

    docs = store.get_all_documents()
    assert len(docs) == 1 and docs[0]["filename"] == "regional_labor_rates.txt"

    # content reconstruction
    assert "110 dollars" in store.get_document_content("regional_labor_rates.txt")

    emb = _hash_embed("neveda mechanical labor cap hourly rate")
    matches = store.search_similarity(emb, "neveda mechanical labor cap hourly rate", top_k=4)
    assert matches, "expected at least one retrieval hit"
    first = matches[0]
    # keep the exact interface shape SQLiteVectorStore returns
    for key in ("id", "content", "filename", "file_type", "score"):
        assert key in first, f"missing result key {key!r}"
    assert first["filename"] == "regional_labor_rates.txt"

    # claim-scoped retrieval is empty for this global-only doc
    assert store.get_claim_chunks("#2026-00001") == []

    assert store.delete_document("regional_labor_rates.txt") is True
    assert store.get_all_documents() == []
    assert store.search_similarity(emb, "neveda mechanical labor cap hourly rate", top_k=4) == []


@needs_postgres
def test_claim_chunks_return_generated_dossier(store):
    _add_global(store, "police_report.txt", "Stationary 8.4 seconds prior to impact, brake pressure 100 percent.",
                claim_id="#2026-99382")
    chunks = store.get_claim_chunks("#2026-99382")
    assert len(chunks) == 1
    assert chunks[0]["score"] == 1.0
    assert "brake pressure" in chunks[0]["content"]
    # a different claim sees nothing
    assert store.get_claim_chunks("#2026-10492") == []


# --------------------------------------------------------------------------- #
# Schema-level scope uniqueness: identity is (tenant_id, claim_id, filename)
# --------------------------------------------------------------------------- #

@needs_postgres
def test_same_filename_coexists_across_claims_and_global(store):
    text_a = "Custom equipment cap 3500 dollars per occurrence."
    text_b = "Custom equipment cap 5000 dollars per occurrence."
    text_global = "Global custom equipment policy text."
    _add_global(store, "report.txt", text_a, claim_id="#2026-30291")
    # The same filename in a different claim is a distinct document, not a conflict.
    _add_global(store, "report.txt", text_b, claim_id="#2026-55912")
    _add_global(store, "report.txt", text_global)

    assert len(store.get_claim_documents("#2026-30291")) == 1
    assert len(store.get_claim_documents("#2026-55912")) == 1
    assert len(store.get_all_documents()) == 1
    assert store.get_document_content("report.txt", "#2026-30291") == text_a
    assert store.get_document_content("report.txt", "#2026-55912") == text_b
    assert store.get_document_content("report.txt", None) == text_global

    # Same-scope overwrite is allowed and replaces only that scope's contents.
    _add_global(store, "report.txt", "Overwritten custom equipment text.", claim_id="#2026-30291")
    assert len(store.get_claim_documents("#2026-30291")) == 1
    assert "Overwritten" in store.get_document_content("report.txt", "#2026-30291")
    assert store.get_document_content("report.txt", "#2026-55912") == text_b

    # Deleting one scope leaves the others intact.
    assert store.delete_document("report.txt", "#2026-30291") is True
    assert store.get_claim_documents("#2026-30291") == []
    assert store.get_document_content("report.txt", "#2026-55912") == text_b
    assert store.get_document_content("report.txt", None) == text_global


@needs_postgres
def test_global_and_claim_scope_uniqueness_enforced_in_schema(store):
    # The unique index prevents two global rows with the same filename.
    _add_global(store, "guide.txt", "Global guideline one.")
    _add_global(store, "guide.txt", "Global guideline two.")  # same scope -> overwrite
    docs = store.get_all_documents()
    assert len(docs) == 1
    assert "two" in store.get_document_content("guide.txt")


# --------------------------------------------------------------------------- #
# Tenant scoping (different tenants are invisible to each other via the store)
# --------------------------------------------------------------------------- #

@needs_postgres
def test_tenant_isolation_between_stores(provisioned_db, tmp_path):
    from backend.postgres_store import PostgresVectorStore

    tenant_a = "test-iso-a-" + os.urandom(3).hex()
    tenant_b = "test-iso-b-" + os.urandom(3).hex()
    a = PostgresVectorStore(dsn=provisioned_db, tenant_id=tenant_a, storage_dir=str(tmp_path / "a"))
    b = PostgresVectorStore(dsn=provisioned_db, tenant_id=tenant_b, storage_dir=str(tmp_path / "b"))
    try:
        _add_global(a, "secret_a.txt", "Tenant A confidential guideline contents.")
        assert a.get_all_documents()[0]["filename"] == "secret_a.txt"
        assert b.get_all_documents() == []
        # searching in B must not surface A's document
        hits = b.search_similarity(_hash_embed("confidential guideline"), "confidential guideline", top_k=4)
        assert not any(m["filename"] == "secret_a.txt" for m in hits)
    finally:
        for t in (tenant_a, tenant_b):
            conn = _connect(provisioned_db)
            try:
                conn.execute("DELETE FROM documents WHERE tenant_id = %s", (t,))
                conn.commit()
            finally:
                conn.close()


@needs_postgres
def test_rls_proven_with_restricted_role(provisioned_db):
    """Row-level security: a non-owner role with the GUC set to tenant A must
    see nothing of tenant B's rows -- even though the rows share a database."""
    conn = _connect(provisioned_db)
    role = "rag_test_reader"
    tenant_a = "test-rls-a"
    tenant_b = "test-rls-b"
    try:
        # seed one row per tenant as the owner (RLS bypassed for the owner)
        conn.autocommit = True
        for t in (tenant_a, tenant_b):
            conn.execute(
                "DELETE FROM documents WHERE tenant_id = %s", (t,)
            )
            conn.execute(
                """
                INSERT INTO documents (tenant_id, filename, file_type, file_size, uploaded_at, claim_id)
                VALUES (%s, %s, 'txt', 12, '2026-01-01 00:00:00', NULL)
                """,
                (t, f"{t}.txt"),
            )
        from psycopg import sql

        # create (idempotently) a restricted reader role and grant SELECT + usage
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
            conn.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        for table in ("documents", "parent_chunks", "child_chunks"):
            conn.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(
                    sql.Identifier(table), sql.Identifier(role)
                )
            )

        # now prove isolation as the restricted role
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_a,))
        n = conn.execute("SELECT count(*) FROM documents WHERE tenant_id = %s", (tenant_a,)).fetchone()[0]
        assert n == 1
        n_b = conn.execute("SELECT count(*) FROM documents WHERE tenant_id = %s", (tenant_b,)).fetchone()[0]
        assert n_b == 0, "tenant B leaked into tenant A under RLS"
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.autocommit = True
        for t in (tenant_a, tenant_b):
            conn.execute("DELETE FROM documents WHERE tenant_id = %s", (t,))
        conn.close()


# --------------------------------------------------------------------------- #
# Failure atomicity & result shape
# --------------------------------------------------------------------------- #

@needs_postgres
def test_embedding_dimension_mismatch_rolls_back(store):
    """A child embedding of the wrong dimension aborts without leaving a partial
    document behind (the insert and its rows must roll back together)."""
    class _WrongDimEmbedder:
        def embed_chunks(self, chunks):
            return [np.ones(3, dtype=np.float32) for _ in chunks]

    with pytest.raises(ValueError):
        store.add_document("bad_dim.txt", "txt", 9, "some content here", _WrongDimEmbedder())
    assert store.get_all_documents() == []
    assert store.get_claim_documents("#2026-00001") == []


@needs_postgres
def test_result_ids_are_parent_ids(store):
    _add_global(store, "glass_endorsement.txt",
                "Zero deductible glass endorsement waives the comprehensive deductible for safety glass.")
    hits = store.search_similarity(
        _hash_embed("glass deductible waiver"),
        "glass deductible waiver",
        top_k=3,
    )
    assert hits
    conn = _connect(POSTGRES_DSN)
    try:
        valid = {r[0] for r in conn.execute("SELECT id FROM parent_chunks WHERE tenant_id = %s", (store.tenant_id,))}
        assert all(m["id"] in valid for m in hits), "search returned non-parent ids"
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Phase 3 async ingestion -- worker against the Postgres data plane
# --------------------------------------------------------------------------- #

@needs_postgres
def test_ingestion_worker_indexes_into_postgres(provisioned_db, tmp_path):
    """The Phase 3 ingestion worker drives PostgresVectorStore end-to-end: blob
    -> parse -> embed -> transactional upsert, with the result searchable via
    pgvector. This is the 'incremental pgvector upsert' leg of milestone 3.1."""
    import uuid

    from backend.blob_store import LocalDocumentBlobStore
    from backend.ingestion_worker import INDEXED, IngestionWorker
    from backend.postgres_store import PostgresVectorStore
    from backend.queue import InProcessQueue

    tenant = f"test-{uuid.uuid4().hex[:8]}"
    blob = LocalDocumentBlobStore(tmp_path / "blobs")
    store = PostgresVectorStore(
        dsn=provisioned_db,
        tenant_id=tenant,
        storage_dir=str(tmp_path / "docs"),
        embedding_dimensions=384,
        blob_store=blob,
    )
    queue = InProcessQueue()
    text = "Nevada mechanical labor cap is 110 dollars per hour."
    blob.put("global/labor.txt", text.encode(), "text/plain")
    queue.enqueue(
        {
            "job_id": "job_1",
            "tenant_id": tenant,
            "filename": "labor.txt",
            "claim_id": None,
            "blob_key": "global/labor.txt",
            "etag": "abc",
        }
    )
    worker = IngestionWorker(
        queue=queue,
        vector_store=store,
        blob_store=blob,
        embedding_engine_factory=_FakeEmbedder,
    )
    try:
        assert worker.process_message(queue.dequeue()) == INDEXED
        docs = store.get_all_documents()
        assert len(docs) == 1 and docs[0]["filename"] == "labor.txt"
        hits = store.search_similarity(_hash_embed("labor rate Nevada"), "labor rate Nevada", top_k=3)
        assert hits and hits[0]["filename"] == "labor.txt"
        assert queue.pending_count == 0
    finally:
        conn = _connect(provisioned_db)
        try:
            conn.execute("DELETE FROM documents WHERE tenant_id = %s", (tenant,))
            conn.commit()
        finally:
            conn.close()


@needs_postgres
def test_ingestion_worker_embed_failure_leaves_no_half_state_in_postgres(provisioned_db, tmp_path):
    """An embedding failure inside the worker retries and never leaves a partial
    document searchable -- the transaction rolls back on the Postgres side too."""
    import uuid

    from backend.blob_store import LocalDocumentBlobStore
    from backend.ingestion_worker import REJECTED, IngestionWorker
    from backend.postgres_store import PostgresVectorStore
    from backend.queue import InProcessQueue

    tenant = f"test-{uuid.uuid4().hex[:8]}"
    blob = LocalDocumentBlobStore(tmp_path / "blobs")
    store = PostgresVectorStore(
        dsn=provisioned_db,
        tenant_id=tenant,
        storage_dir=str(tmp_path / "docs"),
        embedding_dimensions=384,
        blob_store=blob,
    )
    queue = InProcessQueue()
    blob.put("global/labor.txt", b"Nevada labor cap 110 dollars.", "text/plain")
    queue.enqueue(
        {
            "job_id": "job_1",
            "tenant_id": tenant,
            "filename": "labor.txt",
            "claim_id": None,
            "blob_key": "global/labor.txt",
            "etag": "abc",
        }
    )

    class _BrokenEmbedder:
        def embed_chunks(self, chunks):
            raise RuntimeError("embedding engine down")

    worker = IngestionWorker(
        queue=queue,
        vector_store=store,
        blob_store=blob,
        embedding_engine_factory=_BrokenEmbedder,
        max_retries=2,
        backoff_base_seconds=0.0,
    )
    try:
        assert worker.process_message(queue.dequeue()) == REJECTED
        conn = _connect(provisioned_db)
        try:
            n = conn.execute("SELECT count(*) FROM documents WHERE tenant_id = %s", (tenant,)).fetchone()[0]
            assert n == 0, "failed embed left a searchable document row (half-state!)"
        finally:
            conn.close()
    finally:
        conn = _connect(provisioned_db)
        try:
            conn.execute("DELETE FROM documents WHERE tenant_id = %s", (tenant,))
            conn.commit()
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Claim rerank candidates (parity with SQLite, tests/test_claim_rerank_candidates.py)
# --------------------------------------------------------------------------- #

_ALONG = np.eye(384, dtype=np.float32)[0]
_ACROSS = np.eye(384, dtype=np.float32)[1]


class _AxisEmbedder:
    """Global docs point along the query; the claim statement is orthogonal,
    so it ranks last on the vector leg and falls outside a small pool."""

    def embed_chunks(self, chunks):
        return [_ACROSS if "deer" in c else _ALONG for c in chunks]

    def embed_query(self, query):
        return _ALONG


class _PreferStatementReranker:
    def __init__(self):
        self.seen = []

    def rerank(self, query, passages, top_k=4):
        self.seen = [p["filename"] for p in passages]
        scored = [dict(p, rerank_score=2.0 if "statement" in p["filename"] else 1.0) for p in passages]
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]


@needs_postgres
def test_claim_document_outside_the_pool_still_reaches_the_reranker(store):
    embedder = _AxisEmbedder()
    for name in ("SOP_A.txt", "SOP_B.txt", "SOP_C.txt"):
        text = f"{name} checklist indicators referral"
        store.add_document(name, "txt", len(text), text, embedder)
    for claim, text in (("C1", "a deer ran into the road at 2:40 AM"), ("C2", "a deer on another claim")):
        store.add_document(f"loss_statement_{claim}.txt", "txt", len(text), text, embedder, claim_id=claim)
    query = "Run the SIU indicator checklist and say whether a referral is indicated"
    reranker = _PreferStatementReranker()

    results = store.search_similarity(_ALONG, query, claim_id="C1", reranking_engine=reranker, top_k=2, candidate_pool=2)
    assert results[0]["filename"] == "loss_statement_C1.txt"
    assert "loss_statement_C2.txt" not in reranker.seen

    store.search_similarity(_ALONG, query, claim_id="C1", reranking_engine=reranker, top_k=2, candidate_pool=2, use_fts=False)
    assert "loss_statement_C1.txt" not in reranker.seen


class _GlassAcrossEmbedder:
    """Filler docs point along the query and the glass endorsement is
    orthogonal, so only the keyword leg can bring it into a small pool."""

    def embed_chunks(self, chunks):
        return [_ACROSS if "Windshield" in c else _ALONG for c in chunks]

    def embed_query(self, query):
        return _ALONG


@needs_postgres
def test_keyword_leg_matches_question_missing_some_words(store):
    # Regression (parity with tests/test_rag_engine.py): websearch_to_tsquery
    # ANDed every quoted token, so "cracked" and "what" -- absent from the
    # chunk -- emptied the keyword leg. The OR'ed terms match it.
    embedder = _GlassAcrossEmbedder()
    for i in range(6):
        text = f"Filler note {i} about towing yards and storage invoices."
        store.add_document(f"filler{i}.txt", "txt", len(text), text, embedder)
    glass = "Zero-deductible glass endorsement. Windshield replacement waives the comprehensive deductible."
    store.add_document("glass.txt", "txt", len(glass), glass, embedder)

    q = "What is the deductible for a cracked windshield? Not covered OR near"
    results = store.search_similarity(_ALONG, q, top_k=3, candidate_pool=3)
    assert "glass.txt" in [r["filename"] for r in results]
