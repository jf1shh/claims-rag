"""One-time migration of the SQLite corpus (`rag_store.db`) into Postgres + pgvector.

Phase 1 (docs/enterprise-migration.md, milestone 1.3) data-plane migration tool:
copies each document's metadata, parent chunks, child chunks, and embeddings from
the SQLite store into `documents`/`parent_chunks`/`child_chunks` under the
configured tenant, preserving the original embeddings (it does NOT re-embed --
that keeps the corpus stable across the cutover). Upserts are idempotent:
re-running replaces an already-migrated (tenant, claim, filename) scope.

Run after `alembic upgrade head`:

    POSTGRES_DSN=postgresql://user:pass@host:5432/rag \
    TENANT_ID=local-development .venv/bin/python pg_migrate.py [--db rag_store.db]

Exits non-zero if any checksum gate fails (row-count drift, embedding
dimension mismatch, or orphaned children/parents in the migrated tenant).

Moves only the database-facing index; the physical source files already live in
stored_documents/ and keep being served from there.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

import numpy as np  # noqa: E402


def _migrate(dsn: str, tenant_id: str, db_path: str, embedding_dimensions: int) -> int:
    import psycopg
    from pgvector.psycopg import register_vector

    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    docs = src.execute(
        "SELECT id, filename, file_type, file_size, uploaded_at, claim_id FROM documents"
    ).fetchall()
    parents = {
        doc_id: src.execute(
            "SELECT id, chunk_index, content FROM parent_chunks WHERE document_id = ? ORDER BY chunk_index",
            (doc_id,),
        ).fetchall()
        for doc_id in {row["id"] for row in docs}
    }
    children = {
        p_id: src.execute(
            "SELECT id, parent_id, content, embedding FROM child_chunks WHERE parent_id = ?",
            (p_id,),
        ).fetchall()
        for doc_parents in parents.values()
        for p_id in (row["id"] for row in doc_parents)
    }
    src.close()

    conn = psycopg.connect(dsn, autocommit=False)
    register_vector(conn)
    conn.execute("SELECT set_config(%s, %s, false)", ("app.tenant_id", tenant_id))

    doc_count = parent_count = child_count = 0
    new_doc_ids = set()
    try:
        for d in docs:
            # Idempotent scope overwrite: drop any prior (tenant, claim, filename).
            conn.execute(
                """
                DELETE FROM documents
                WHERE tenant_id = %s AND filename = %s AND claim_id IS NOT DISTINCT FROM %s
                """,
                (tenant_id, d["filename"], d["claim_id"]),
            )
            new_doc_id = conn.execute(
                """
                INSERT INTO documents (tenant_id, filename, file_type, file_size, uploaded_at, claim_id)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (tenant_id, d["filename"], d["file_type"], d["file_size"],
                 d["uploaded_at"], d["claim_id"]),
            ).fetchone()[0]
            new_doc_ids.add(new_doc_id)
            doc_count += 1

            for p in parents[d["id"]]:
                new_parent_id = conn.execute(
                    """
                    INSERT INTO parent_chunks (tenant_id, document_id, chunk_index, content)
                    VALUES (%s, %s, %s, %s) RETURNING id
                    """,
                    (tenant_id, new_doc_id, p["chunk_index"], p["content"]),
                ).fetchone()[0]
                parent_count += 1

                for c in children[p["id"]]:
                    vec = np.frombuffer(c["embedding"], dtype=np.float32)
                    if vec.shape[0] != embedding_dimensions:
                        conn.rollback()
                        raise SystemExit(
                            f"child embedding dim {vec.shape[0]} != {embedding_dimensions} "
                            f"(doc {d['filename']!r}, child {c['id']}); aborting without commit"
                        )
                    conn.execute(
                        """
                        INSERT INTO child_chunks (tenant_id, parent_id, content, embedding)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (tenant_id, new_parent_id, c["content"], vec),
                    )
                    child_count += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    print(f"Migrated {doc_count} documents / {parent_count} parent chunks / {child_count} child "
          f"chunks (tenant {tenant_id!r})")

    # --- Checksum gates ----------------------------------------------------- #
    ok = True
    for label, got, want in (
        ("documents", _pg_count(conn, "SELECT count(*) FROM documents WHERE tenant_id = %s", tenant_id), doc_count),
        ("parent chunks", _pg_count(conn, "SELECT count(*) FROM parent_chunks WHERE tenant_id = %s", tenant_id), parent_count),
        ("child chunks", _pg_count(conn, "SELECT count(*) FROM child_chunks WHERE tenant_id = %s", tenant_id), child_count),
    ):
        status = "OK " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  {status} {label}: migrated {got} == inserted {want}")

    orphans = _pg_count(
        conn,
        """
        SELECT count(*)
        FROM child_chunks c
        WHERE c.tenant_id = %s
          AND (NOT EXISTS (SELECT 1 FROM parent_chunks p WHERE p.id = c.parent_id)
               OR NOT EXISTS (SELECT 1 FROM parent_chunks p JOIN documents d ON p.document_id = d.id
                              WHERE p.id = c.parent_id))
        """,
        tenant_id,
    )
    status = "OK " if orphans == 0 else "FAIL"
    if orphans != 0:
        ok = False
    print(f"  {status} orphaned child chunks: {orphans}")

    conn.close()
    print(f"\nMIGRATION {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _pg_count(conn, sql, tenant_id):
    return conn.execute(sql, (tenant_id,)).fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description="Migrate rag_store.db into Postgres + pgvector.")
    parser.add_argument("--db", default=str(REPO_ROOT / "rag_store.db"), help="source SQLite database path")
    parser.add_argument("--dimensions", type=int, default=384, help="embedding dimensions (must match vector(N) column)")
    args = parser.parse_args()

    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        sys.exit("POSTGRES_DSN is required (postgresql+psycopg:// or postgresql:// URL)")
    tenant = os.environ.get("TENANT_ID", "local-development")
    if not os.path.exists(args.db):
        sys.exit(f"source database not found: {args.db}")

    sys.exit(_migrate(dsn, tenant, args.db, args.dimensions))


if __name__ == "__main__":
    main()
