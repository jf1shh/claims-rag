"""Postgres + pgvector storage backend (Phase 1 of docs/enterprise-migration.md).

Implements the same `VectorStore` interface as `SQLiteVectorStore`
(`backend/rag_engine.py`) so nothing above the seam changes -- callers in
`backend/app.py`, `backend/agentic_router.py`, the ingest path, and the parity
harness all consume the interface. The schema (Alembic migration
`alembic/versions/0001_initial_enterprise_schema.py`) carries the lessons from
the SQLite implementation forwards:

  * `(tenant_id, claim_id, filename)` unique, NULLS NOT DISTINCT -- a
    cross-claim filename collision is impossible in the schema, not just in
    app code (the schema-level fix the per-scope overwrite guard was only a
    stopgap for in SQLite).
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

import os
import time
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit

import numpy as np


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

    # ------------------------------------------------------------------ #
    # Connection helpers
    # ------------------------------------------------------------------ #

    def _connect(self):
        """Opens a psycopg connection bound to this tenant's RLS GUC.

        Every connection in this class goes through here so the row-level
        security policy (keyed on `app.tenant_id`) is satisfied and pgvector's
        numpy<->vector adapters are registered. `set_config(..., false)` scopes
        the value to the whole session (not just the current transaction), so
        RLS applies across subsequent statements.
        """
        import psycopg
        from pgvector.psycopg import register_vector

        conn = psycopg.connect(self.dsn, autocommit=False)
        register_vector(conn)
        conn.execute("SELECT set_config(%s, %s, false)", ("app.tenant_id", self.tenant_id))
        return conn

    # ------------------------------------------------------------------ #
    # Interface: add / delete / list / chunk
    # ------------------------------------------------------------------ #

    def add_document(self, filename, file_type, file_size, text, embedding_engine, claim_id=None, file_path=None):
        """Stage immutable source bytes, then publish their reference with the index.

        A failed stage or transaction leaves the previous indexed version intact.
        Unreferenced staged blobs can be reclaimed by the maintenance command.
        """
        from backend.source_storage import stage_source

        from backend.rag_engine import TextChunker, safe_filename

        filename = safe_filename(filename)
        os.makedirs(self.storage_dir, exist_ok=True)
        uploaded_at = time.strftime("%Y-%m-%d %H:%M:%S")

        conn = self._connect()
        try:
            # Scope guard (parity with SQLite). Because the schema now allows the
            # same filename in different scopes, an overwrite must only clobber
            # the SAME scope -- never a sibling claim's or the global set's copy.
            existing = conn.execute(
                """
                SELECT id, claim_id FROM documents
                WHERE tenant_id = %s AND filename = %s
                """,
                (self.tenant_id, filename),
            ).fetchall()
            if existing:
                existing_claim = existing[0][1]
                if not (existing_claim is None and claim_id is None) and existing_claim != claim_id:
                    existing_scope = f"claim {existing_claim}" if existing_claim else "global guidelines"
                    new_scope = f"claim {claim_id}" if claim_id else "global guidelines"
                    raise ValueError(
                        f"'{filename}' already exists in {existing_scope}; refusing to overwrite it "
                        f"from {new_scope}. Rename the uploaded file (or delete the existing one) first "
                        "-- claim folders and global guidelines must not overwrite each other."
                    )
                # Same scope -> overwrite: delete its rows (cascade removes chunks).
                conn.execute(
                    """
                    DELETE FROM documents
                    WHERE tenant_id = %s AND filename = %s
                      AND claim_id IS NOT DISTINCT FROM %s
                    """,
                    (self.tenant_id, filename, claim_id),
                )

            row = conn.execute(
                """
                INSERT INTO documents (tenant_id, filename, file_type, file_size, uploaded_at, claim_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (self.tenant_id, filename, file_type, file_size, uploaded_at, claim_id),
            ).fetchone()
            doc_id = row[0]

            parent_chunks = TextChunker.chunk(text, chunk_size=1200, chunk_overlap=200)

            for p_idx, p_text in enumerate(parent_chunks):
                p_row = conn.execute(
                    """
                    INSERT INTO parent_chunks (tenant_id, document_id, chunk_index, content)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (self.tenant_id, doc_id, p_idx, p_text),
                ).fetchone()
                p_id = p_row[0]

                child_chunks = TextChunker.chunk(p_text, chunk_size=250, chunk_overlap=50)
                if not child_chunks:
                    continue
                embeddings = embedding_engine.embed_chunks(child_chunks)

                for c_text, embedding in zip(child_chunks, embeddings, strict=True):
                    vec = np.asarray(embedding, dtype=np.float32)
                    if vec.shape[0] != self.embedding_dimensions:
                        raise ValueError(
                            f"embedding dimension {vec.shape[0]} does not match configured "
                            f"{self.embedding_dimensions}"
                        )
                    conn.execute(
                        """
                        INSERT INTO child_chunks (tenant_id, parent_id, content, embedding)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (self.tenant_id, p_id, c_text, vec),
                    )

            storage_key, document_version = stage_source(self, filename, file_type, text, file_path)
            conn.execute("UPDATE documents SET storage_key = %s, document_version = %s WHERE id = %s AND tenant_id = %s",
                         (storage_key, document_version, doc_id, self.tenant_id))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        return doc_id, len(parent_chunks)

    def delete_document(self, filename) -> bool:
        """Removes a document and (cascade) its chunks/embeddings/FTS rows by
        filename within this tenant. False if no such document exists. The
        physical file is removed only after the delete commits."""
        from backend.rag_engine import safe_filename

        filename = safe_filename(filename)
        source_key = self.get_blob_key(filename)
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM documents WHERE tenant_id = %s AND filename = %s RETURNING id",
                (self.tenant_id, filename),
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
             "id": pid, "document_id": str(did), "document_version": version or "legacy-unversioned"}
            for c, f, t, pid, did, version in rows
        ]

    def get_document_content(self, filename) -> str:
        """Reconstructs a document's full text by joining its parent chunks in order."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT p.content
                FROM parent_chunks p
                JOIN documents d ON p.document_id = d.id
                WHERE d.tenant_id = %s AND d.filename = %s
                ORDER BY p.chunk_index ASC
                """,
                (self.tenant_id, filename),
            ).fetchall()
        finally:
            conn.close()
        return "\n\n".join(r[0] for r in rows)

    def get_document_metadata(self, filename):
        from backend.rag_engine import safe_filename
        conn = self._connect()
        try:
            row = conn.execute("SELECT id, claim_id, filename, storage_key, document_version FROM documents WHERE tenant_id = %s AND filename = %s", (self.tenant_id, safe_filename(filename))).fetchone()
            return {"document_id": str(row[0]), "claim_id": row[1], "filename": row[2], "storage_key": row[3], "document_version": row[4] or "legacy-unversioned"} if row else None
        finally:
            conn.close()

    def get_blob_key(self, filename):
        from backend.rag_engine import _blob_key
        metadata = self.get_document_metadata(filename)
        if metadata is None:
            return None
        return metadata["storage_key"] or _blob_key(metadata["claim_id"], filename)


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
                        r[0]: (r[1], r[2], r[3], r[4], r[5])
                        for r in conn.execute(
                            """
                            SELECT p.id, p.content, d.filename, d.file_type, d.id, d.document_version
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
                         "document_id": str(meta[pid][3]), "document_version": meta[pid][4] or "legacy-unversioned"}
                        for pid, sim in top
                        if pid in meta
                    ]
            except Exception:
                # Degrade gracefully to the keyword leg only (mirrors SQLite's
                # `except sqlite3.OperationalError: pass` behavior).
                vector_ranked = []

            # --- 2. Keyword leg: Postgres FTS, quoted tokens + ts_rank order.  #
            # Token-quoting keeps operator words (and/or/not) literal (AND).
            tokens = [t for t in query_text.split() if t.isalnum()]
            fts_ranked: List[dict] = []
            if use_fts and tokens:
                clean_query = " ".join(f'"{t}"' for t in tokens)
                try:
                    fts_rows = conn.execute(
                        """
                        SELECT p.id, p.content, d.filename, d.file_type, d.id, d.document_version
                        FROM parent_chunks p
                        JOIN documents d ON p.document_id = d.id
                        WHERE p.tenant_id = %(t)s
                          AND (d.claim_id IS NULL OR d.claim_id = %(c)s)
                          AND p.content_tsv @@ websearch_to_tsquery('english', %(q)s)
                        ORDER BY ts_rank(p.content_tsv, websearch_to_tsquery('english', %(q)s)) DESC
                        LIMIT 40
                        """,
                        {"t": self.tenant_id, "c": claim_id, "q": clean_query},
                    ).fetchall()
                    fts_ranked = [
                        {"id": r[0], "content": r[1], "filename": r[2], "file_type": r[3], "score": 0.0,
                         "document_id": str(r[4]), "document_version": r[5] or "legacy-unversioned"}
                        for r in fts_rows
                    ]
                except Exception:
                    pass

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
        finally:
            conn.close()

        # --- 4. Cross-encoder rerank (same protocol as SQLite) ------------- #
        if reranking_engine and candidates:
            reranked = reranking_engine.rerank(query_text, candidates, top_k=top_k)
            for item in reranked:
                item["score"] = item["rerank_score"]
            return reranked
        return candidates[:top_k]
