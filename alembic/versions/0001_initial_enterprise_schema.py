"""initial enterprise schema -- Postgres + pgvector data plane

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-28
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIMENSIONS = 384  # all-MiniLM-L6-v2

_TABLES = ("documents", "parent_chunks", "child_chunks")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # documents -- metadata for one uploaded source file. The scope uniqueness
    # constraint is NULLS NOT DISTINCT so that multiple GLOBAL (claim_id IS NULL)
    # uploads can't collide, while per-claim filenames are still scoped within a
    # tenant. This is the schema-level guarantee that a cross-claim filename
    # collision is impossible (SQLite only enforced that in application code).
    op.execute(
        """
        CREATE TABLE documents (
            id          bigserial PRIMARY KEY,
            tenant_id   text NOT NULL,
            filename    text NOT NULL,
            file_type   text,
            file_size   bigint,
            uploaded_at text,
            claim_id    text
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_documents_scope "
        "ON documents (tenant_id, claim_id, filename) NULLS NOT DISTINCT"
    )
    op.execute("CREATE INDEX ix_documents_tenant ON documents (tenant_id)")

    # parent_chunks -- larger context blocks backing both FTS and returned context.
    op.execute(
        """
        CREATE TABLE parent_chunks (
            id          bigserial PRIMARY KEY,
            tenant_id   text NOT NULL,
            document_id bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            chunk_index integer,
            content     text NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_parent_chunks_document ON parent_chunks (document_id)")
    op.execute("CREATE INDEX ix_parent_chunks_tenant ON parent_chunks (tenant_id)")

    # child_chunks -- smaller embeddings for precise vector matching, mapped up
    # to their parent for context. embedding is a pgvector vector(384).
    op.execute(
        f"""
        CREATE TABLE child_chunks (
            id        bigserial PRIMARY KEY,
            tenant_id text NOT NULL,
            parent_id bigint NOT NULL REFERENCES parent_chunks (id) ON DELETE CASCADE,
            content   text NOT NULL,
            embedding vector({EMBEDDING_DIMENSIONS}) NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_child_chunks_parent ON child_chunks (parent_id)")
    op.execute("CREATE INDEX ix_child_chunks_tenant ON child_chunks (tenant_id)")

    # Approximate-nearest-neighbor (HNSW) index on child embeddings. The parity
    # harness (docs/enterprise-migration.md Phase 0/1) defines the acceptable
    # divergence -- recall@k >= 0.9 -- versus SQLite's exact brute-force search.
    op.execute(
        "CREATE INDEX ix_child_chunks_embedding_hnsw "
        "ON child_chunks USING hnsw (embedding vector_cosine_ops)"
    )

    # Postgres FTS replacing the FTS5 keyword leg: a generated tsvector column +
    # GIN index on parent chunk content.
    op.execute(
        "ALTER TABLE parent_chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_parent_chunks_content_gin ON parent_chunks USING gin (content_tsv)"
    )

    # Row-level security. Each table's policy keys rows on the session GUC
    # `app.tenant_id` (set via SELECT set_config('app.tenant_id', :id, false)).
    # A connection whose GUC is unset sees nothing (current_setting returns
    # NULL), so the safe default is deny. Policies are enforced for non-owner
    # database roles; the store sets the GUC on every connection AND filters by
    # tenant_id in SQL, so tenancy is enforced regardless of role ownership.
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::text)"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP EXTENSION IF EXISTS vector")
