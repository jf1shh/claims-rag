"""Postgres + pgvector storage backend (Phase 1 of docs/enterprise-migration.md).

Implements the same `VectorStore` interface as `SQLiteVectorStore`
(`backend/rag_engine.py`) so nothing above the seam changes -- callers in
`backend/app.py`, `backend/agentic_router.py`, the ingest path, and the parity
harness all consume the interface. The schema (Alembic migration
`alembic/versions/0001_initial_enterprise_schema.py`) carries the lessons from
the SQLite implementation forwards:

  * `(tenant_id, claim_id, filename)` unique, NULLS NOT DISTINCT -- a
    cross-claim filename collision is impossible in the schema, not just in
    app code. SQLite enforces the same scoping via `ix_documents_scope`
    instead of a runtime guard.
  * `tenant_id` on every row with row-level security keyed on the
    `app.tenant_id` GUC. RLS is defense in depth -- this store *also* filters
    by tenant_id in SQL -- and it is proven by tests/test_postgres_store.py via
    a restricted role (`SET ROLE`), because a connection-as-owner otherwise
    bypasses RLS.
  * HNSW pgvector index on child embeddings (approximate, vs SQLite's exact
    brute-force matrix search) -- the parity harness defines the acceptable
    divergence (recall@k >= 0.9).
  * Postgres FTS (`websearch_to_tsquery` + a generated `content_tsv` GIN index)
    replaces the FTS5 keyword leg. Query tokens are quoted so operator words
    (and/or/not) are treated as literal terms -- the FTS5 token-quoting lesson.

Retrieval math is byte-for-byte the SQLite recipe: vector leg maps every child
to its max-scoring parent, keyword leg returns the top 40 by ts_rank, both fuse
via Reciprocal Rank Fusion (k=60) over a candidate pool of `max(15, top_k)`,
then the same cross-encoder reranker optionally re-sorts. Physical source files
are written to `storage_dir` only after the DB transaction commits (the
Phase 16 ordering fix), so a rolled-back write can never leave disk out of sync
with the index.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit

import numpy as np

logger = logging.getLogger(__name__)


class _DiscardUncommitted(Exception):
    """Thrown into the pool's checkout context so it rolls back, not commits."""


class _PooledConnection:
    """Proxies a pooled psycopg connection so every existing call site's
    `conn = self._connect() ... finally: conn.close()` keeps working
    unchanged. `close()` returns the physical connection to the pool instead
    of closing the socket, and keeps plain `psycopg.Connection.close()`
    semantics: work not explicitly committed is rolled back, never committed
    (the pool's own context exit would commit it).
    """

    __slots__ = ("_cm", "_conn")

    def __init__(self, cm, conn):
        self._cm = cm
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self) -> None:
        from psycopg.pq import TransactionStatus

        if self._conn.info.transaction_status == TransactionStatus.IDLE:
            self._cm.__exit__(None, None, None)
            return
        # Open, failed or broken transaction: exit with an error so the pool
        # rolls back (logging, not raising, if the rollback itself fails) and
        # still checks the connection back in.
        exc = _DiscardUncommitted()
        self._cm.__exit__(_DiscardUncommitted, exc, None)


def _redact_dsn(dsn: str) -> str:
    """Returns the DSN with any password removed (for display in /api/status)."""
    parts = urlsplit(dsn)
    if not parts.netloc or "@" not in parts.netloc:
        return dsn
    userinfo, _, host = parts.netloc.rpartition("@")
    user = userinfo.split(":", 1)[0] if userinfo else ""
    return urlunsplit((parts.scheme, f"{user}@{host}", parts.path, parts.query, parts.fragment))


class PostgresVectorStore:
    """pgvector-backed VectorStore scoped to one tenant.

    Callers depend only on the `VectorStore` interface (`backend/rag_engine.py`).
    The `embedding_dimensions` must match the configured embedding model
    (`all-MiniLM-L6-v2` -> 384) and the `vector(N)` column in the schema.
    """

    def __init__(
        self,
        dsn: str,
        tenant_id: str = "local-development",
        storage_dir: Optional[str] = None,
        embedding_dimensions: int = 384,
        blob_store=None,
        pool_connect_timeout_seconds: int = 5,
        pool_statement_timeout_ms: int = 30000,
        pool_max_size: int = 10,
        pool_timeout_seconds: float = 10.0,
    ):
        from backend.rag_engine import STORED_DOCUMENTS_DIR

        if not dsn:
            raise ValueError("dsn must not be empty")
        if not tenant_id:
            raise ValueError("tenant_id must not be empty")
        self.dsn = dsn
        self.tenant_id = tenant_id
        self.storage_dir = storage_dir or STORED_DOCUMENTS_DIR
        self.embedding_dimensions = embedding_dimensions
        # Optional object-storage adapter (Phase 2, S3). When set, add/delete
        # route source bytes through it; when None, legacy filesystem behavior.
        self.blob_store = blob_store
        # Exposed for /api/status (path) parity with SQLiteVectorStore.db_path;
        # PG credentials must never be rendered, so keep it redacted.
        self.db_path = _redact_dsn(dsn)
        self.pool_connect_timeout_seconds = pool_connect_timeout_seconds
        self.pool_statement_timeout_ms = pool_statement_timeout_ms
        self.pool_max_size = pool_max_size
        self.pool_timeout_seconds = pool_timeout_seconds
        # Created lazily on first use (see _get_pool) so constructing a store
        # that never touches the DB (scripts, offline tests) opens nothing.
        self._pool = None
        self._pool_lock = threading.Lock()
        # Set once the BYPASSRLS/superuser check has run on a real connection,
        # so a long-lived store only logs the warning (if any) a single time.
        self._rls_bypass_checked = False

    # ------------------------------------------------------------------ #
    # Connection helpers
    # ------------------------------------------------------------------ #

    def _configure_connection(self, conn) -> None:
        """Runs once per new physical connection the pool opens: registers
        pgvector's numpy<->vector adapters and sets this tenant's RLS GUC.
        `set_config(..., false)` scopes the value to the whole session (not
        just the current transaction), so RLS applies across every statement
        run on this physical connection for as long as the pool keeps it.
        Must leave the connection idle -- the pool checks a configured
        connection back in immediately after this returns.
        """
        from pgvector.psycopg import register_vector

        register_vector(conn)
        conn.execute("SELECT set_config(%s, %s, false)", ("app.tenant_id", self.tenant_id))
        conn.commit()
        if not self._rls_bypass_checked:
            self._check_rls_bypass(conn)

    def _check_rls_bypass(self, conn) -> None:
        """Warns once if the connecting role bypasses row-level security
        (superuser or BYPASSRLS), since RLS -- even FORCEd -- never applies
        to such a role: tenant isolation would then rely on query filters
        alone. Never raises -- a failed check must not fail startup."""
        self._rls_bypass_checked = True
        try:
            row = conn.execute(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
            conn.commit()
        except Exception as exc:
            logger.warning("RLS bypass check failed (%s)", type(exc).__name__)
            return
        if row and row[0]:
            logger.warning(
                "Postgres role bypasses row-level security; tenant isolation relies on "
                "query filters only. Use a non-superuser role without BYPASSRLS."
            )

    def _get_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    from psycopg_pool import ConnectionPool

                    self._pool = ConnectionPool(
                        self.dsn,
                        min_size=1,
                        max_size=self.pool_max_size,
                        timeout=self.pool_timeout_seconds,
                        kwargs={
                            "connect_timeout": self.pool_connect_timeout_seconds,
                            "options": f"-c statement_timeout={self.pool_statement_timeout_ms}",
                            "autocommit": False,
                        },
                        configure=self._configure_connection,
                        check=ConnectionPool.check_connection,
                        open=True,
                    )
        return self._pool

    def _connect(self):
        """Checks out a pooled connection bound to this tenant's RLS GUC.

        Every connection in this class goes through here so the pool's
        `connect_timeout`/`statement_timeout` and the RLS GUC (set once per
        physical connection in `_configure_connection`) apply uniformly. The
        pool is created lazily, thread-safely, on first checkout. Returns a
        `_PooledConnection` proxy, used exactly like the plain connection this
        method used to return directly (`conn = self._connect() ...
        finally: conn.close()`) -- `close()` returns it to the pool instead
        of closing the socket. A starved pool raises `psycopg_pool.PoolTimeout`
        instead of hanging.
        """
        cm = self._get_pool().connection()
        conn = cm.__enter__()
        return _PooledConnection(cm, conn)

    def close(self) -> None:
        """Closes the pool, if one was ever created. Idempotent."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    # ------------------------------------------------------------------ #
    # Interface: add / delete / list / chunk
    # ------------------------------------------------------------------ #

    def add_document(self, filename, file_type, file_size, text, embedding_engine, claim_id=None, file_path=None):
        """Stage immutable source bytes, then publish their reference with the index.

        A failed stage or transaction leaves the previous indexed version intact.
        Unreferenced staged blobs can be reclaimed by the maintenance command.

        Chunking, embedding, and source staging all run BEFORE any connection
        is opened -- embedding a large document can take seconds and staging
        is an S3 PUT or an fsync, and running either inside the transaction
        held the row lock that whole time. The transaction below only covers
        the scope check and the inserts themselves. Staged keys are unique
        per call, so staging before the transaction is safe; if the
        transaction then fails, the just-staged blob is deleted since nothing
        references it.
        """
        from backend.rag_engine import TextChunker, safe_filename
        from backend.source_storage import discard_staged, stage_source

        filename = safe_filename(filename)
        os.makedirs(self.storage_dir, exist_ok=True)
        uploaded_at = time.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Generate parent/child chunks and embeddings up front, with no
        # connection open.
        parent_chunks = TextChunker.chunk(text, chunk_size=1200, chunk_overlap=200)
        parent_payloads = []
        for p_idx, p_text in enumerate(parent_chunks):
            child_chunks = TextChunker.chunk(p_text, chunk_size=250, chunk_overlap=50)
            child_payloads = []
            if child_chunks:
                embeddings = embedding_engine.embed_chunks(child_chunks)
                for c_text, embedding in zip(child_chunks, embeddings, strict=True):
                    vec = np.asarray(embedding, dtype=np.float32)
                    if vec.shape[0] != self.embedding_dimensions:
                        raise ValueError(
                            f"embedding dimension {vec.shape[0]} does not match configured "
                            f"{self.embedding_dimensions}"
                        )
                    child_payloads.append((c_text, vec))
            parent_payloads.append((p_idx, p_text, child_payloads))

        storage_key, document_version = stage_source(self, filename, file_type, text, file_path)

        try:
            conn = self._connect()
        except Exception:
            discard_staged(self, storage_key, "staged-source")
            raise
        try:
            # Overwrite only the existing row in the SAME scope -- the schema's
            # (tenant_id, claim_id, filename) NULLS NOT DISTINCT unique index
            # already makes a cross-scope collision impossible, so this is a
            # plain scoped delete-then-insert, not a guard.
            replaced = conn.execute(
                """
                DELETE FROM documents
                WHERE tenant_id = %s AND filename = %s
                  AND claim_id IS NOT DISTINCT FROM %s
                RETURNING storage_key
                """,
                (self.tenant_id, filename, claim_id),
            ).fetchone()
            replaced_storage_key = replaced[0] if replaced else None

            row = conn.execute(
                """
                INSERT INTO documents (tenant_id, filename, file_type, file_size, uploaded_at, claim_id, storage_key, document_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (self.tenant_id, filename, file_type, file_size, uploaded_at, claim_id, storage_key, document_version),
            ).fetchone()
            doc_id = row[0]

            for p_idx, p_text, child_payloads in parent_payloads:
                p_row = conn.execute(
                    """
                    INSERT INTO parent_chunks (tenant_id, document_id, chunk_index, content)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (self.tenant_id, doc_id, p_idx, p_text),
                ).fetchone()
                p_id = p_row[0]

                for c_text, vec in child_payloads:
                    conn.execute(
                        """
                        INSERT INTO child_chunks (tenant_id, parent_id, content, embedding)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (self.tenant_id, p_id, c_text, vec),
                    )

            conn.commit()
        except Exception as e:
            conn.rollback()
            discard_staged(self, storage_key, "staged-source")
            raise e
        finally:
            conn.close()

        # Now that the new version is durably committed, delete the replaced
        # row's blob. A failure here must not fail the upload -- the new
        # document is already indexed and searchable. Only app-written
        # `versions/` blobs are removed here; legacy plain files are left to
        # scripts/collect_source_garbage.py.
        if replaced_storage_key and replaced_storage_key.startswith("versions/"):
            discard_staged(self, replaced_storage_key, "replaced-version")

        return doc_id, len(parent_chunks)

    def delete_document(self, filename, claim_id=None) -> bool:
        """Removes the document in the given scope (claim_id, filename) within
        this tenant, and (cascade) its chunks/embeddings/FTS rows. False if no
        such document exists. The physical file is removed only after the
        delete commits."""
        from backend.rag_engine import safe_filename

        filename = safe_filename(filename)
        source_key = self.get_blob_key(filename, claim_id)
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                DELETE FROM documents
                WHERE tenant_id = %s AND filename = %s AND claim_id IS NOT DISTINCT FROM %s
                RETURNING id
                """,
                (self.tenant_id, filename, claim_id),
            )
            deleted = cur.rowcount > 0
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        if deleted and source_key:
            from backend.source_storage import source_store
            source_store(self).delete(source_key)
        return deleted

    def get_all_documents(self):
        """Global (claim_id IS NULL) documents in this tenant, newest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT filename, file_type, file_size, uploaded_at
                FROM documents
                WHERE tenant_id = %s AND claim_id IS NULL
                ORDER BY uploaded_at DESC
                """,
                (self.tenant_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            {"filename": r[0], "file_type": r[1], "file_size": r[2], "uploaded_at": r[3]}
            for r in rows
        ]

    def get_claim_documents(self, claim_id):
        """Documents attached to one claim in this tenant, newest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT filename, file_type, file_size, uploaded_at
                FROM documents
                WHERE tenant_id = %s AND claim_id = %s
                ORDER BY uploaded_at DESC
                """,
                (self.tenant_id, claim_id),
            ).fetchall()
        finally:
            conn.close()
        return [
            {"filename": r[0], "file_type": r[1], "file_size": r[2], "uploaded_at": r[3]}
            for r in rows
        ]

    def get_claim_chunks(self, claim_id):
        """Every parent chunk of this tenant's claim-scoped documents, unranked
        (score=1.0 sentinel) -- the guaranteed claim-dossier context fix."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT p.content, d.filename, d.file_type, p.id, d.id, d.document_version
                FROM parent_chunks p
                JOIN documents d ON p.document_id = d.id
                WHERE d.tenant_id = %s AND d.claim_id = %s
                ORDER BY d.filename, p.chunk_index
                """,
                (self.tenant_id, claim_id),
            ).fetchall()
        finally:
            conn.close()
        return [
            {"content": c, "filename": f, "file_type": t, "score": 1.0,
             "id": pid, "document_id": str(did), "document_version": version or "legacy-unversioned",
             "claim_id": claim_id}
            for c, f, t, pid, did, version in rows
        ]

    def get_document_content(self, filename, claim_id=None) -> str:
        """Reconstructs a document's full text by joining its parent chunks in order."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT p.content
                FROM parent_chunks p
                JOIN documents d ON p.document_id = d.id
                WHERE d.tenant_id = %s AND d.filename = %s AND d.claim_id IS NOT DISTINCT FROM %s
                ORDER BY p.chunk_index ASC
                """,
                (self.tenant_id, filename, claim_id),
            ).fetchall()
        finally:
            conn.close()
        return "\n\n".join(r[0] for r in rows)

    def get_document_metadata(self, filename, claim_id=None):
        from backend.rag_engine import safe_filename
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, claim_id, filename, storage_key, document_version FROM documents WHERE tenant_id = %s AND filename = %s AND claim_id IS NOT DISTINCT FROM %s",
                (self.tenant_id, safe_filename(filename), claim_id),
            ).fetchone()
            return {"document_id": str(row[0]), "claim_id": row[1], "filename": row[2], "storage_key": row[3], "document_version": row[4] or "legacy-unversioned"} if row else None
        finally:
            conn.close()

    def get_blob_key(self, filename, claim_id=None):
        from backend.rag_engine import _blob_key
        metadata = self.get_document_metadata(filename, claim_id)
        if metadata is None:
            return None
        return metadata["storage_key"] or _blob_key(metadata["claim_id"], filename)


    def _claim_ranked(self, conn, query, claim_id) -> List[dict]:
        """The active claim's parents, best vector score first, capped at
        CLAIM_RERANK_CANDIDATES. Same shape as the vector leg's items."""
        from backend.rag_engine import CLAIM_RERANK_CANDIDATES

        try:
            with conn.transaction():
                rows = conn.execute(
                    """
                    SELECT p.id, p.content, d.filename, d.file_type, d.id, d.document_version, d.claim_id,
                           max(1.0 - (c.embedding <=> %(q)s)) AS sim
                    FROM child_chunks c
                    JOIN parent_chunks p ON c.parent_id = p.id
                    JOIN documents d ON p.document_id = d.id
                    WHERE c.tenant_id = %(t)s AND d.claim_id = %(c)s
                    GROUP BY p.id, d.id
                    ORDER BY sim DESC
                    LIMIT %(lim)s
                    """,
                    {"q": query, "t": self.tenant_id, "c": claim_id, "lim": CLAIM_RERANK_CANDIDATES},
                ).fetchall()
        except Exception as exc:
            logger.warning("Claim candidate query failed, reranking the fused pool only: %s", type(exc).__name__)
            return []
        return [
            {"id": r[0], "content": r[1], "filename": r[2], "file_type": r[3], "score": float(r[7]),
             "document_id": str(r[4]), "document_version": r[5] or "legacy-unversioned", "claim_id": r[6]}
            for r in rows
        ]

    def search_similarity(self, query_embedding, query_text, claim_id=None, reranking_engine=None, top_k=15, use_fts=True, candidate_pool=50):
        """Hybrid retrieval with RRF and optional cross-encoder rerank, scoped by
        tenant and (optionally) claim. Result shape identical to SQLiteVectorStore.
        use_fts=False returns a pure vector-only baseline (the eval harness's
        `naive` mode)."""
        conn = self._connect()
        try:
            query = np.asarray(query_embedding, dtype=np.float32)
            pool = candidate_pool

            # --- 1. Vector leg: max child cosine per parent (exact, matching
            # the SQLite brute-force behavior) ------------------------------ #
            vector_ranked: List[dict] = []
            try:
                # Each leg runs in its own savepoint: a failure here must not
                # abort the outer transaction and take the keyword leg down
                # with it (a plain aborted-transaction error would otherwise
                # make every subsequent statement on `conn` fail too).
                with conn.transaction():
                    top = conn.execute(
                        """
                        SELECT c.parent_id, max(1.0 - (c.embedding <=> %(q)s)) AS sim
                        FROM child_chunks c
                        JOIN parent_chunks p ON c.parent_id = p.id
                        JOIN documents d ON p.document_id = d.id
                        WHERE c.tenant_id = %(t)s
                          AND (d.claim_id IS NULL OR d.claim_id = %(c)s)
                        GROUP BY c.parent_id
                        ORDER BY sim DESC
                        LIMIT %(lim)s
                        """,
                        {"q": query, "t": self.tenant_id, "c": claim_id, "lim": pool},
                    ).fetchall()
                    if top:
                        ids = [r[0] for r in top]
                        meta = {
                            r[0]: (r[1], r[2], r[3], r[4], r[5], r[6])
                            for r in conn.execute(
                                """
                                SELECT p.id, p.content, d.filename, d.file_type, d.id, d.document_version, d.claim_id
                                FROM parent_chunks p
                                JOIN documents d ON p.document_id = d.id
                                WHERE p.tenant_id = %s AND p.id = ANY(%s)
                                """,
                                (self.tenant_id, ids),
                            ).fetchall()
                        }
                        vector_ranked = [
                            {"id": pid, "content": meta[pid][0], "filename": meta[pid][1],
                             "file_type": meta[pid][2], "score": float(sim),
                             "document_id": str(meta[pid][3]), "document_version": meta[pid][4] or "legacy-unversioned",
                             "claim_id": meta[pid][5]}
                            for pid, sim in top
                            if pid in meta
                        ]
            except Exception as exc:
                # Degrade gracefully to the keyword leg only (mirrors SQLite's
                # `except sqlite3.OperationalError: pass` behavior).
                logger.warning("Vector search leg failed, falling back to keyword-only: %s", type(exc).__name__)
                vector_ranked = []

            # --- 2. Keyword leg: Postgres FTS, quoted OR'ed terms + ts_rank. #
            # Same terms as the SQLite leg; websearch_to_tsquery reads a bare
            # `or` between quoted terms as OR (see fts_match_query).
            from backend.rag_engine import fts_match_query

            clean_query = fts_match_query(query_text, joiner=" or ")
            fts_ranked: List[dict] = []
            if use_fts and clean_query:
                try:
                    with conn.transaction():
                        fts_rows = conn.execute(
                            """
                            SELECT p.id, p.content, d.filename, d.file_type, d.id, d.document_version, d.claim_id
                            FROM parent_chunks p
                            JOIN documents d ON p.document_id = d.id
                            WHERE p.tenant_id = %(t)s
                              AND (d.claim_id IS NULL OR d.claim_id = %(c)s)
                              AND p.content_tsv @@ websearch_to_tsquery('english', %(q)s)
                            -- Normalization 1 (rank / (1 + log(length))) mirrors bm25's
                            -- length term; ts_rank has no IDF, the remaining gap to SQLite.
                            ORDER BY ts_rank(p.content_tsv, websearch_to_tsquery('english', %(q)s), 1) DESC
                            LIMIT 40
                            """,
                            {"t": self.tenant_id, "c": claim_id, "q": clean_query},
                        ).fetchall()
                        fts_ranked = [
                            {"id": r[0], "content": r[1], "filename": r[2], "file_type": r[3], "score": 0.0,
                             "document_id": str(r[4]), "document_version": r[5] or "legacy-unversioned",
                             "claim_id": r[6]}
                            for r in fts_rows
                        ]
                except Exception as exc:
                    logger.warning("Keyword search leg failed, falling back to vector-only: %s", type(exc).__name__)

            # --- 3. RRF fusion (k=60) -------------------------------------- #
            k_const = 60
            rrf_scores: dict = {}
            parent_info: dict = {}
            for ranked in (vector_ranked, fts_ranked):
                for rank, item in enumerate(ranked):
                    p_id = item["id"]
                    rrf_scores[p_id] = rrf_scores.get(p_id, 0.0) + (1.0 / (k_const + rank + 1))
                    parent_info[p_id] = item

            fused = []
            for p_id, score in rrf_scores.items():
                meta = parent_info[p_id]
                fused.append(
                    {**meta, "id": p_id, "content": meta["content"], "filename": meta["filename"],
                     "file_type": meta["file_type"], "score": score}
                )
            fused.sort(key=lambda x: x["score"], reverse=True)
            candidates = fused[:candidate_pool]

            # The vector leg is LIMITed to the pool, so fetch the active claim's
            # own best parents separately for the rerank (see add_claim_candidates).
            if reranking_engine and use_fts and claim_id is not None:
                from backend.rag_engine import add_claim_candidates

                candidates = add_claim_candidates(candidates, self._claim_ranked(conn, query, claim_id))
        finally:
            conn.close()

        from backend.rag_engine import pin_named_documents

        # --- 4. Cross-encoder rerank (same protocol as SQLite) ------------- #
        if reranking_engine and candidates:
            reranked = reranking_engine.rerank(query_text, candidates, top_k=len(candidates))
            for item in reranked:
                item["score"] = item["rerank_score"]
            if not use_fts:
                return reranked[:top_k]
            return pin_named_documents(query_text, reranked, top_k)

        if not use_fts:
            return candidates[:top_k]
        return pin_named_documents(query_text, candidates, top_k)
