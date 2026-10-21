"""Force row-level security so even table-owner/BYPASSRLS-less roles cannot
skip the tenant_isolation policies (enterprise audit finding #25). Without
FORCE, RLS enabled by 0001 still exempts the table owner by default."""
from alembic import op

# Same table list 0001 enabled RLS on; module names starting with a digit
# cannot be imported as a dotted path, so this is a deliberate copy.
_TABLES = ("documents", "parent_chunks", "child_chunks")

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade():
    for table in reversed(_TABLES):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
